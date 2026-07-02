# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only session domain queries."""

from __future__ import annotations

from uuid import UUID

from apps.sessions.models import AgentSession, AgentSessionEvent, AgentSessionEventKind


def get_session_name(session_id: UUID) -> str | None:
    return AgentSession.objects.filter(pk=session_id).values_list('name', flat=True).first()


def get_first_input_text(session_id: UUID) -> str | None:
    payload = (
        AgentSessionEvent.objects.filter(session_id=session_id, kind=AgentSessionEventKind.INPUT)
        .order_by('seq')
        .values_list('payload', flat=True)
        .first()
    )
    if not payload:
        return None
    content = payload.get('content', '')
    if not isinstance(content, str):
        return None
    text = content.strip()
    return text or None


def input_event_count(session_id: UUID) -> int:
    return AgentSessionEvent.objects.filter(
        session_id=session_id,
        kind=AgentSessionEventKind.INPUT,
    ).count()
