# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django ORM + Redis session backend for production agent runs."""

from __future__ import annotations

import copy
import importlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from apps.agents.models import Agent
from apps.bus.channels import mailbox_drain
from apps.runner.backends.base import RecordedActivity, SessionBackend
from apps.sessions.activities import ACTIVITY_UPDATE_UNSET
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
)
from apps.sessions.rebuild import rebuild_messages
from apps.sessions.services import commands as session_commands
from apps.sessions.services import queries as session_queries
from apps.sessions.services.commands import record_input as record_input_command
from libs.agent_spec import AgentConfigSpec


def _recorded_from_row(row: AgentSessionActivity) -> RecordedActivity:
    """Copy a session-service row into a detached backend activity snapshot."""
    return RecordedActivity(
        id=row.id,
        session_id=row.session_id,
        parent_id=row.parent_id,
        seq=row.seq,
        revision=row.revision,
        kind=row.kind,
        status=row.status,
        name=row.name,
        summary=row.summary,
        details=copy.deepcopy(row.details or {}),
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        child_session_id=row.child_session_id,
    )


class DjangoSessionBackend(SessionBackend):
    """Production backend delegating activity access to session services."""

    def __init__(self, session: AgentSession) -> None:
        """Bind the backend to one already-authorized session."""
        self._session = session

    @property
    def session_id(self) -> uuid.UUID:
        return self._session.id

    @property
    def session(self) -> AgentSession:
        """Expose the bound session for existing runner setup logic."""
        return self._session

    @property
    def user_id(self) -> int:
        """Return the owning user used for runtime credential resolution."""
        return self._session.user_id

    def get_spec(self) -> AgentConfigSpec:
        """Load the session's pinned agent configuration."""
        if self._session.agent_config_id is None:
            raise ValueError('algorithm sessions do not have an agent configuration')
        return self._session.agent_config.get_spec()

    def get_status(self) -> str:
        """Return the bound session's current status."""
        return self._session.status

    def set_status(self, status: str) -> None:
        """Persist status and reconcile a linked parent reference atomically."""
        session_commands.set_session_status(self._session, status)

    def set_ended_at(self, when: datetime) -> None:
        """Persist the session completion timestamp."""
        self._session.ended_at = when
        self._session.save(update_fields=['ended_at'])

    def rebuild_messages(self, *, system_prompt: str) -> list[dict[str, Any]]:
        """Delegate provider reconstruction to the canonical sessions service."""
        return rebuild_messages(self._session, system_prompt=system_prompt)

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
        """Create through sessions commands, which also own publication."""
        row = session_commands.create_activity(
            self._session,
            kind=kind,
            status=status,
            name=name,
            summary=summary,
            details=details,
            parent_id=parent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            child_session_id=child_session_id,
        )
        return _recorded_from_row(row)

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
        """Update only an activity owned by this backend's session.

        Activity session ownership is immutable, so the service query remains
        valid when the command subsequently locks and mutates the same row.
        """
        if not session_queries.activity_belongs_to_session(self._session, activity_id):
            raise ValueError('activity does not belong to backend session')
        row = session_commands.update_activity(
            activity_id,
            status=status,
            summary=summary,
            details=details,
            model=ACTIVITY_UPDATE_UNSET if model is None else model,
            input_tokens=ACTIVITY_UPDATE_UNSET if input_tokens is None else input_tokens,
            output_tokens=ACTIVITY_UPDATE_UNSET if output_tokens is None else output_tokens,
            cost_usd=ACTIVITY_UPDATE_UNSET if cost_usd is None else cost_usd,
            latency_ms=ACTIVITY_UPDATE_UNSET if latency_ms is None else latency_ms,
        )
        return _recorded_from_row(row)

    def activities(self) -> list[RecordedActivity]:
        """Return detached activity snapshots in canonical sequence order."""
        return [_recorded_from_row(row) for row in session_queries.activities_for(self._session)]

    def publish_activity(self, activity: RecordedActivity) -> None:
        """Avoid duplicate transport writes because sessions commands publish."""
        del activity

    def record_input(self, content: str) -> RecordedActivity:
        """Persist input through the command retaining chat-name side effects."""
        return _recorded_from_row(record_input_command(self._session, content))

    def link_subagent(
        self,
        *,
        agent_id: uuid.UUID,
        name: str,
        summary: str,
        details: dict[str, Any],
        parent_id: uuid.UUID | None = None,
    ) -> RecordedActivity:
        """Start a linked child through the atomic sessions command."""
        try:
            agent = Agent.objects.get(pk=agent_id)
        except Agent.DoesNotExist as exc:
            raise ValueError('child agent does not exist') from exc
        # Resolve lazily to avoid the dispatch -> task -> loop -> backend import
        # cycle while still failing setup before the atomic command writes.
        dispatch_session = importlib.import_module('apps.runner.dispatch').maybe_dispatch_session
        child = session_commands.start_linked_child_session(
            parent_session=self._session,
            parent_activity_id=parent_id,
            agent=agent,
            name=name,
            summary=summary,
            details=details,
            dispatch_callback=dispatch_session,
        )
        return _recorded_from_row(session_queries.subagent_activity_for_child(child.id))

    def drain_mailbox(self) -> list[dict[str, Any]]:
        """Drain pending control messages for the bound session."""
        return mailbox_drain(self._session.id)
