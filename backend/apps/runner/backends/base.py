# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session persistence and control-plane interface for the runner loop."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from libs.agent_spec import AgentConfigSpec


@dataclass(frozen=True)
class RecordedActivity:
    """Provider-neutral snapshot mirroring the canonical activity stream."""

    id: uuid.UUID
    session_id: uuid.UUID
    parent_id: uuid.UUID | None
    seq: int
    revision: int
    kind: str
    status: str
    name: str
    summary: str
    details: dict[str, Any]
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None
    child_session_id: uuid.UUID | None = None

    def to_stream_dict(self) -> dict[str, Any]:
        """Serialize this snapshot into the canonical activity upsert shape."""
        return {
            'id': str(self.id),
            'session_id': str(self.session_id),
            'parent_id': str(self.parent_id) if self.parent_id else None,
            'seq': self.seq,
            'revision': self.revision,
            'kind': self.kind,
            'status': self.status,
            'name': self.name,
            'summary': self.summary,
            'details': self.details,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost_usd': str(self.cost_usd) if self.cost_usd is not None else None,
            'latency_ms': self.latency_ms,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'child_session_id': str(self.child_session_id) if self.child_session_id else None,
        }


class SessionBackend(ABC):
    """Abstract activity persistence, mailbox, and session state for the runner."""

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
        """Persist and publish a new activity under this session."""
        raise NotImplementedError

    @abstractmethod
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
        """Revise and publish an existing activity."""
        raise NotImplementedError

    @abstractmethod
    def activities(self) -> list[RecordedActivity]:
        """Return immutable activity snapshots in creation order."""
        raise NotImplementedError

    @abstractmethod
    def publish_activity(self, activity: RecordedActivity) -> None:
        """Run the backend's activity publication hook."""
        raise NotImplementedError

    @abstractmethod
    def record_input(self, content: str) -> RecordedActivity:
        """Create and publish one terminal user-input activity."""
        raise NotImplementedError

    @abstractmethod
    def link_subagent(
        self,
        *,
        agent_id: uuid.UUID,
        name: str,
        summary: str,
        details: dict[str, Any],
        parent_id: uuid.UUID | None = None,
    ) -> RecordedActivity:
        """Create and dispatch a child session represented by one activity."""
        raise NotImplementedError

    @abstractmethod
    def drain_mailbox(self) -> list[dict[str, Any]]:
        """Drain pending session control messages."""
        raise NotImplementedError

    @property
    @abstractmethod
    def user_id(self) -> int:
        """Session owner for credential resolution. Every agent run has a user."""
        raise NotImplementedError
