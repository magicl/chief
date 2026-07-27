# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-memory session backend for CLI runs and unit tests (no DB or Redis)."""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import Any

from apps.runner.backends.base import RecordedActivity, SessionBackend
from apps.sessions.rebuild import rebuild_messages_from_activities
from libs.agent_spec import AgentConfigSpec

_VALID_KINDS = frozenset({'input', 'output', 'tool', 'llm', 'span', 'status', 'subagent', 'failure', 'restart'})
_VALID_STATUSES = frozenset({'pending', 'running', 'succeeded', 'failed', 'cancelled'})
_TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'cancelled'})
_LIFECYCLE_KINDS = frozenset({'tool', 'llm', 'span', 'subagent'})


class MemorySessionBackend(SessionBackend):
    """Django-free backend with canonical lifecycle and publication parity.

    Linked subagents are reference-only simulations: no child backend is
    created or dispatched, and later child-status reconciliation is unavailable.
    """

    def __init__(
        self,
        spec: AgentConfigSpec,
        *,
        user_id: int,
        session_id: uuid.UUID | None = None,
    ) -> None:
        """In-memory backend for CLI runs and tests.

        ``user_id`` is required: credential resolution always goes through
        ``apps.keys`` (which still falls back to env for LLM defaults).
        """
        self._session_id = session_id or uuid.uuid4()
        self._spec = spec
        self._user_id = user_id
        self._status = 'queued'
        self._activities: list[RecordedActivity] = []
        self._mailbox: list[dict[str, Any]] = []
        self._published: list[dict[str, Any]] = []
        self._ended_at: datetime | None = None
        # Reentrant because create/update publish while holding the same lock.
        self._lock = RLock()

    @property
    def session_id(self) -> uuid.UUID:
        return self._session_id

    @property
    def user_id(self) -> int:
        """Session owner for credential resolution."""
        return self._user_id

    @property
    def published_activities(self) -> list[dict[str, Any]]:
        """Return every activity revision sent through the memory publish hook."""
        with self._lock:
            return copy.deepcopy(self._published)

    def get_spec(self) -> AgentConfigSpec:
        """Return the immutable agent configuration for this run."""
        return self._spec

    def get_status(self) -> str:
        """Return the current in-memory session status."""
        return self._status

    def set_status(self, status: str) -> None:
        """Set the current in-memory session status."""
        self._status = status

    def set_ended_at(self, when: datetime) -> None:
        """Store the in-memory session completion timestamp."""
        self._ended_at = when

    def rebuild_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        """Reconstruct provider messages from canonical conversational activities."""
        with self._lock:
            activities = [_copy_activity(activity) for activity in self._activities]
        return rebuild_messages_from_activities(activities, system_prompt=system_prompt)

    def create_activity(
        self,
        *,
        kind: str,
        status: str,
        name: str,
        summary: str,
        details: dict[str, Any],
        parent_id: uuid.UUID | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
        child_session_id: uuid.UUID | None = None,
    ) -> RecordedActivity:
        """Create, validate, store, and publish one activity snapshot."""
        with self._lock:
            self._validate_kind_and_status(kind, status)
            if child_session_id is not None and kind != 'subagent':
                raise ValueError('child session links are allowed only on subagent activities')
            if parent_id is not None and not any(activity.id == parent_id for activity in self._activities):
                raise ValueError('parent activity must belong to this session')
            if child_session_id is not None and any(
                activity.child_session_id == child_session_id for activity in self._activities
            ):
                raise ValueError('child session is already linked')
            now = _now()
            started_at = now if status == 'running' else None
            ended_at = now if status in _TERMINAL_STATUSES and kind in _LIFECYCLE_KINDS else None
            activity = RecordedActivity(
                id=uuid.uuid4(),
                session_id=self._session_id,
                parent_id=parent_id,
                seq=len(self._activities) + 1,
                revision=1,
                kind=kind,
                status=status,
                name=name,
                summary=summary,
                details=copy.deepcopy(details),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                started_at=started_at,
                ended_at=ended_at,
                created_at=now,
                child_session_id=child_session_id,
            )
            self._activities.append(activity)
            self.publish_activity(activity)
            return _copy_activity(activity)

    def update_activity(
        self,
        activity_id: uuid.UUID,
        *,
        status: str | None = None,
        summary: str | None = None,
        details: dict[str, Any] | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> RecordedActivity:
        """Revise one activity while enforcing terminal immutability."""
        with self._lock:
            index = next(
                (position for position, activity in enumerate(self._activities) if activity.id == activity_id),
                None,
            )
            if index is None:
                raise KeyError(activity_id)
            current = self._activities[index]
            if current.status in _TERMINAL_STATUSES:
                raise ValueError('terminal activity is immutable')
            next_status = current.status if status is None else status
            if next_status not in _VALID_STATUSES:
                raise ValueError('invalid activity status')
            updated = replace(
                current,
                revision=current.revision + 1,
                status=next_status,
                summary=current.summary if summary is None else summary,
                details=current.details if details is None else copy.deepcopy(details),
                model=current.model if model is None else model,
                input_tokens=current.input_tokens if input_tokens is None else input_tokens,
                output_tokens=current.output_tokens if output_tokens is None else output_tokens,
                cost_usd=current.cost_usd if cost_usd is None else cost_usd,
                latency_ms=current.latency_ms if latency_ms is None else latency_ms,
                ended_at=(current.ended_at or (_now() if next_status in _TERMINAL_STATUSES else None)),
            )
            self._activities[index] = updated
            self.publish_activity(updated)
            return _copy_activity(updated)

    def activities(self) -> list[RecordedActivity]:
        """Return current activity revisions in immutable sequence order."""
        with self._lock:
            return [_copy_activity(activity) for activity in self._activities]

    def publish_activity(self, activity: RecordedActivity) -> None:
        """Capture a canonical upsert payload for tests and CLI observers."""
        with self._lock:
            self._published.append(copy.deepcopy(activity.to_stream_dict()))

    def record_input(self, content: str) -> RecordedActivity:
        """Create one terminal input activity through the canonical API."""
        return self.create_activity(
            kind='input',
            status='succeeded',
            name='input',
            summary=(content[:120] + '…') if len(content) > 120 else content,
            details={'content': content},
        )

    def link_subagent(
        self,
        *,
        agent_id: uuid.UUID,
        name: str,
        summary: str,
        details: dict[str, Any],
        parent_id: uuid.UUID | None = None,
    ) -> RecordedActivity:
        """Create a reference-only synthetic child link for Django-free parity."""
        child_session_id = uuid.uuid4()
        linked_details = copy.deepcopy(details)
        linked_details.update(
            {
                'child_status': 'queued',
                'memory_link_only': True,
                'requested_agent_id': str(agent_id),
            }
        )
        return self.create_activity(
            kind='subagent',
            status='pending',
            name=name,
            summary=summary,
            details=linked_details,
            parent_id=parent_id,
            child_session_id=child_session_id,
        )

    def drain_mailbox(self) -> list[dict[str, Any]]:
        """Return and clear pending in-memory mailbox messages."""
        messages = list(self._mailbox)
        self._mailbox.clear()
        return messages

    def push_mailbox(self, message: dict[str, Any]) -> None:
        """Test/CLI helper — enqueue a control or chat message."""
        self._mailbox.append(message)

    @staticmethod
    def _validate_kind_and_status(kind: str, status: str) -> None:
        """Reject values outside the canonical activity vocabulary."""
        if kind not in _VALID_KINDS:
            raise ValueError('invalid activity kind')
        if status not in _VALID_STATUSES:
            raise ValueError('invalid activity status')


def memory_backend_for_turn(
    spec: AgentConfigSpec,
    *,
    input_text: str,
    user_id: int,
) -> MemorySessionBackend:
    """Single-turn in-memory session preloaded with one user message."""
    backend = MemorySessionBackend(spec, user_id=user_id)
    backend.record_input(input_text)
    backend.set_status('queued')
    return backend


def _now() -> datetime:
    """Return one timezone-aware timestamp using only the standard library."""
    return datetime.now(UTC)


def _copy_activity(activity: RecordedActivity) -> RecordedActivity:
    """Detach one returned activity from mutable backend detail storage."""
    return replace(activity, details=copy.deepcopy(activity.details))
