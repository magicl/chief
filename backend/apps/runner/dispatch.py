# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Resume dispatch helpers."""

from __future__ import annotations

import logging
from uuid import UUID

from apps.bus.channels import is_locked, mailbox_push
from apps.sessions.models import AgentSession, AgentSessionStatus

logger = logging.getLogger(__name__)

RESUMABLE = frozenset(
    {
        AgentSessionStatus.WAITING,
        AgentSessionStatus.PAUSED,
        AgentSessionStatus.QUEUED,
    }
)


def maybe_dispatch_session(session_id: UUID | str) -> bool:
    """Enqueue ``run_session`` if the session is resumable and no lock is held."""
    try:
        session = AgentSession.objects.get(pk=session_id)
    except AgentSession.DoesNotExist:
        return False

    if session.status not in RESUMABLE:
        return False
    if is_locked(session_id):
        logger.debug('Session %s lock held; skip dispatch', session_id)
        return False

    session.status = AgentSessionStatus.QUEUED
    session.save(update_fields=['status'])

    from apps.runner.tasks import run_session

    run_session.delay(str(session_id))
    return True


def push_chat_and_dispatch(session_id: UUID | str, content: str) -> None:
    mailbox_push(session_id, {'action': 'chat', 'content': content})
    maybe_dispatch_session(session_id)


def push_control_and_maybe_dispatch(session_id: UUID | str, action: str) -> None:
    mailbox_push(session_id, {'action': action})
    if action in ('resume', 'chat'):
        maybe_dispatch_session(session_id)
