# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django ORM + Redis session backend for production agent runs."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from apps.bus.channels import mailbox_drain
from apps.runner.backends.base import RecordedEvent, SessionBackend
from apps.sessions.events import append_event, events_for
from apps.sessions.models import AgentSession, AgentSessionEvent
from apps.sessions.notify import publish_session_event
from apps.sessions.rebuild import rebuild_messages
from apps.sessions.services.commands import record_input as record_input_command
from libs.agent_spec import AgentConfigSpec


def _recorded_from_row(row: AgentSessionEvent) -> RecordedEvent:
    return RecordedEvent(
        seq=row.seq,
        kind=row.kind,
        payload=row.payload or {},
        event_id=row.id,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
    )


class DjangoSessionBackend(SessionBackend):
    def __init__(self, session: AgentSession) -> None:
        self._session = session

    @property
    def session_id(self) -> uuid.UUID:
        return self._session.id

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def user_id(self) -> int:
        return self._session.agent.user_id

    def get_spec(self) -> AgentConfigSpec:
        return self._session.agent_config.get_spec()

    def get_status(self) -> str:
        return self._session.status

    def set_status(self, status: str) -> None:
        self._session.status = status
        self._session.save(update_fields=['status'])

    def set_ended_at(self, when: datetime) -> None:
        self._session.ended_at = when
        self._session.save(update_fields=['ended_at'])

    def rebuild_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        return rebuild_messages(self._session, system_prompt=system_prompt)

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
        row = append_event(
            self._session,
            kind,
            payload,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        return _recorded_from_row(row)

    def record_input(self, content: str) -> RecordedEvent:
        row = record_input_command(self._session, content)
        return _recorded_from_row(row)

    def drain_mailbox(self) -> list[dict[str, Any]]:
        return mailbox_drain(self._session.id)

    def publish_event(self, event: RecordedEvent) -> None:
        publish_session_event(self._session.id, event.to_stream_dict(session_id=self._session.id))

    def events(self) -> list[RecordedEvent]:
        return [_recorded_from_row(row) for row in events_for(self._session)]
