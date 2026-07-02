# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-memory session backend for CLI runs and unit tests (no DB or Redis)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from apps.agents.spec import AgentConfigSpec
from apps.runner.backends.base import RecordedEvent, SessionBackend
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from apps.sessions.rebuild import rebuild_messages_from_events
from django.utils import timezone


class MemorySessionBackend(SessionBackend):
    def __init__(self, spec: AgentConfigSpec, *, session_id: uuid.UUID | None = None) -> None:
        self._session_id = session_id or uuid.uuid4()
        self._spec = spec
        self._status: str = AgentSessionStatus.QUEUED
        self._events: list[RecordedEvent] = []
        self._mailbox: list[dict[str, Any]] = []
        self._published: list[dict[str, Any]] = []
        self._ended_at: datetime | None = None

    @property
    def session_id(self) -> uuid.UUID:
        return self._session_id

    @property
    def published_events(self) -> list[dict[str, Any]]:
        return list(self._published)

    def get_spec(self) -> AgentConfigSpec:
        return self._spec

    def get_status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status

    def set_ended_at(self, when: datetime) -> None:
        self._ended_at = when

    def rebuild_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        return rebuild_messages_from_events(self._events, system_prompt=system_prompt)

    def append_event(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> RecordedEvent:
        event = RecordedEvent(
            seq=len(self._events) + 1,
            kind=kind,
            payload=payload,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            created_at=_now(),
        )
        self._events.append(event)
        return event

    def drain_mailbox(self) -> list[dict[str, Any]]:
        messages = list(self._mailbox)
        self._mailbox.clear()
        return messages

    def push_mailbox(self, message: dict[str, Any]) -> None:
        """Test/CLI helper — enqueue a control or chat message."""
        self._mailbox.append(message)

    def record_input(self, content: str) -> RecordedEvent:
        event = self.append_event(AgentSessionEventKind.INPUT, {'content': content})
        self.publish_event(event)
        return event

    def publish_event(self, event: RecordedEvent) -> None:
        self._published.append(event.to_stream_dict(session_id=self._session_id))

    def events(self) -> list[RecordedEvent]:
        return list(self._events)


def memory_backend_for_turn(spec: AgentConfigSpec, *, input_text: str) -> MemorySessionBackend:
    """Single-turn in-memory session preloaded with one user message."""
    backend = MemorySessionBackend(spec)
    backend.append_event(AgentSessionEventKind.INPUT, {'content': input_text})
    backend.set_status(AgentSessionStatus.QUEUED)
    return backend


def _now() -> datetime:
    now = timezone.now()
    if timezone.is_naive(now):
        return now.replace(tzinfo=ZoneInfo('UTC'))
    return now
