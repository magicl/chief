# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Scoped activity recorder backed by a runner session backend."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Any
from uuid import UUID

from apps.runner.backends.base import RecordedActivity, SessionBackend
from libs.tools.activity import ActivityRef, validate_completion_status

_TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'cancelled'})


def _activity_ref(activity: RecordedActivity) -> ActivityRef:
    """Reduce a backend snapshot to the Django-free recorder handle."""
    return ActivityRef(
        id=activity.id,
        seq=activity.seq,
        revision=activity.revision,
        kind=activity.kind,
        status=activity.status,
    )


class BackendActivityRecorder:
    """Record nested work while isolating parent scopes per execution context.

    ``ContextVar`` scopes flow through nested calls and copied asyncio task
    contexts. New OS threads intentionally start without the caller's scope;
    callers that need inheritance must run under ``contextvars.copy_context``
    or call ``push_parent`` explicitly in the spawned thread.
    """

    def __init__(
        self,
        backend: SessionBackend,
        *,
        on_created: Callable[[RecordedActivity], None] | None = None,
        on_updated: Callable[[RecordedActivity], None] | None = None,
        on_terminal: Callable[[UUID], None] | None = None,
    ) -> None:
        """Bind scoped activity operations and post-persistence observers."""
        self._backend = backend
        self._on_created = on_created
        self._on_updated = on_updated
        self._on_terminal = on_terminal
        self._parents: ContextVar[tuple[UUID | None, ...]] = ContextVar(
            f'activity_parents_{id(self)}',
            default=(),
        )

    def start(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'running',
    ) -> ActivityRef:
        """Create an activity beneath the current context-local parent."""
        stack = self._parents.get()
        parent_id = stack[-1] if stack else None
        activity = self._backend.create_activity(
            kind=kind,
            status=status,
            name=name,
            summary=summary,
            details=details or {},
            parent_id=parent_id,
        )
        try:
            self._notify_created(activity)
        except BaseException:
            if activity.status not in _TERMINAL_STATUSES and not self._is_terminal(activity.id):
                failed = self._backend.update_activity(
                    activity.id,
                    status='failed',
                    summary=f'{name} start interrupted',
                    details={
                        'message': 'Activity start instrumentation interrupted',
                        'code': 'activity_start_interrupted',
                    },
                )
                self._remember_terminal(failed)
                try:
                    self._notify_updated(failed)
                except BaseException:
                    # Preserve the callback fault that interrupted creation; the
                    # compensating update is already durable and terminal.
                    pass
            raise
        self._remember_terminal(activity)
        return _activity_ref(activity)

    def _is_terminal(self, activity_id: UUID) -> bool:
        """Return whether source-aware hook compensation already closed an activity."""
        return any(
            activity.id == activity_id and activity.status in _TERMINAL_STATUSES
            for activity in self._backend.activities()
        )

    def _notify_created(self, activity: RecordedActivity) -> None:
        """Notify observers after a canonical create has persisted."""
        if self._on_created is not None:
            self._on_created(activity)

    def _notify_updated(self, activity: RecordedActivity) -> None:
        """Notify observers after a canonical revision has persisted."""
        if self._on_updated is not None:
            self._on_updated(activity)

    def _remember_terminal(self, activity: RecordedActivity) -> None:
        """Mark terminal persistence before an observer can interrupt control flow."""
        if activity.status in _TERMINAL_STATUSES and self._on_terminal is not None:
            self._on_terminal(activity.id)

    def _update(self, activity_id: UUID, **kwargs: Any) -> ActivityRef:
        """Persist one revision, mark terminal state, then notify observers."""
        activity = self._backend.update_activity(activity_id, **kwargs)
        self._remember_terminal(activity)
        self._notify_updated(activity)
        return _activity_ref(activity)

    def complete(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'succeeded',
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Complete an activity with a terminal status and optional usage."""
        validate_completion_status(status)
        return self._update(
            activity_id,
            status=status,
            summary=summary,
            details=details,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    def fail(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Mark an activity failed while preserving caller-curated metadata."""
        return self._update(
            activity_id,
            status='failed',
            summary=summary,
            details=details,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    def status_note(
        self,
        *,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Emit one terminal status activity beneath the current parent."""
        return self.start(
            kind='status',
            status='succeeded',
            name=name,
            summary=summary,
            details=details,
        )

    @contextmanager
    def span(self, *, name: str, summary: str = '') -> Iterator[ActivityRef]:
        """Complete a scoped span on success or fail it before re-raising."""
        ref = self.start(kind='span', name=name, summary=summary)
        try:
            with self.push_parent(ref.id):
                yield ref
        except BaseException:
            self.fail(ref.id, summary=f'{summary or name} failed')
            raise
        self.complete(ref.id, summary=summary or name)

    @contextmanager
    def push_parent(self, activity_id: UUID | None) -> Iterator[None]:
        """Push a parent override and restore the exact prior stack on exit."""
        token = self._parents.set((*self._parents.get(), activity_id))
        try:
            yield
        finally:
            self._parents.reset(token)

    def link_subagent(
        self,
        *,
        agent_id: UUID,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Create a linked child reference beneath the current parent scope."""
        stack = self._parents.get()
        parent_id = stack[-1] if stack else None
        activity = self._backend.link_subagent(
            agent_id=agent_id,
            name=name,
            summary=summary,
            details=details or {},
            parent_id=parent_id,
        )
        self._notify_created(activity)
        return _activity_ref(activity)
