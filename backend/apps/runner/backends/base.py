# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session persistence and control-plane interface for the runner loop."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from libs.agent_spec import AgentConfigSpec


@dataclass
class RecordedEvent:
    """Provider-neutral event record returned by all session backends."""

    seq: int
    kind: str
    payload: dict[str, Any]
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    created_at: datetime | None = None

    def to_stream_dict(self, *, session_id: uuid.UUID | str) -> dict[str, Any]:
        return {
            'id': str(self.event_id),
            'session_id': str(session_id),
            'seq': self.seq,
            'kind': self.kind,
            'payload': self.payload,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost_usd': str(self.cost_usd) if self.cost_usd is not None else None,
            'latency_ms': self.latency_ms,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SessionBackend(ABC):
    """Abstracts event log, mailbox, status, and realtime publish for SessionRunner."""

    @property
    @abstractmethod
    def session_id(self) -> uuid.UUID:
        raise NotImplementedError

    @abstractmethod
    def get_spec(self) -> AgentConfigSpec:
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def set_status(self, status: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_ended_at(self, when: datetime) -> None:
        raise NotImplementedError

    @abstractmethod
    def rebuild_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def drain_mailbox(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def record_input(self, content: str) -> RecordedEvent:
        raise NotImplementedError

    @abstractmethod
    def publish_event(self, event: RecordedEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def events(self) -> list[RecordedEvent]:
        raise NotImplementedError

    @property
    @abstractmethod
    def user_id(self) -> int | None:
        """Session owner for credential resolution; None means env-only (no DB lookup)."""
        raise NotImplementedError
