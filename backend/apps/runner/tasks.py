# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery task that executes one agent session."""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from apps.bus.channels import release_lock, try_acquire_lock
from apps.runner.loop import SessionRunner
from apps.sessions.events import append_event
from apps.sessions.models import AgentSession, AgentSessionEventKind, AgentSessionStatus
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def run_session(self: Any, session_id: str) -> None:
    token = uuid.uuid4().hex
    if not try_acquire_lock(session_id, token):
        logger.info('Session %s already running; skipping duplicate dispatch', session_id)
        return

    try:
        session = AgentSession.objects.select_related('agent_config', 'agent').get(pk=session_id)
    except AgentSession.DoesNotExist:
        release_lock(session_id, token)
        return

    emit_restart = session.status in (
        AgentSessionStatus.WAITING,
        AgentSessionStatus.PAUSED,
        AgentSessionStatus.RUNNING,
    )

    if session.status == AgentSessionStatus.QUEUED:
        session.status = AgentSessionStatus.RUNNING
        session.started_at = session.started_at or timezone.now()
        session.save(update_fields=['status', 'started_at'])
    elif session.status in (AgentSessionStatus.WAITING, AgentSessionStatus.PAUSED):
        session.status = AgentSessionStatus.RUNNING
        session.save(update_fields=['status'])

    try:
        SessionRunner.for_session(session, emit_restart=emit_restart).run()
        session.refresh_from_db()
        if session.status == AgentSessionStatus.RUNNING:
            session.status = AgentSessionStatus.DONE
            session.ended_at = timezone.now()
            session.save(update_fields=['status', 'ended_at'])
    except Exception:  # pylint: disable=broad-except
        logger.exception('Unhandled failure in session %s', session_id)
        session.refresh_from_db()
        if session.status == AgentSessionStatus.RUNNING:
            append_event(
                session,
                AgentSessionEventKind.FAILURE,
                {
                    'message': 'Unexpected worker failure',
                    'code': 'unexpected_failure',
                    'traceback': traceback.format_exc(),
                },
            )
            session.status = AgentSessionStatus.WAITING
            session.save(update_fields=['status'])
    finally:
        release_lock(session_id, token)
