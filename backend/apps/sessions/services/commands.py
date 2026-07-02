# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session domain mutations."""

from __future__ import annotations

from uuid import UUID

from apps.sessions.events import append_event
from apps.sessions.models import AgentSession, AgentSessionEvent, AgentSessionEventKind
from apps.sessions.notify import publish_session_event, publish_session_update
from apps.sessions.services.queries import input_event_count
from django.db import transaction
from libs.algorithms.chat_name import DEFAULT_CHAT_NAME_CONFIG


def record_input(session: AgentSession, content: str) -> AgentSessionEvent:
    row = append_event(session, AgentSessionEventKind.INPUT, {'content': content})
    publish_session_event(session.id, row.to_stream_dict())
    if input_event_count(session.id) == 1 and DEFAULT_CHAT_NAME_CONFIG.enabled:
        transaction.on_commit(lambda: _schedule_generate_session_name(session.id))
    return row


def update_session_name(session_id: UUID, name: str, *, source: str = 'auto') -> bool:
    del source
    normalized = _normalize_name(name)
    if not normalized:
        return False
    updated = AgentSession.objects.filter(pk=session_id, name__isnull=True).update(name=normalized)
    if updated:
        publish_session_update(session_id, {'name': normalized})
    return bool(updated)


def _normalize_name(name: str, *, max_len: int = 80) -> str:
    text = ' '.join(name.split())
    if not text:
        return ''
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + '…'
    return text


def _schedule_generate_session_name(session_id: UUID) -> None:
    def enqueue() -> None:
        from celery import current_app

        current_app.send_task(
            'apps.sessions.tasks.generate_session_name',
            args=[str(session_id)],
        )

    transaction.on_commit(enqueue)
