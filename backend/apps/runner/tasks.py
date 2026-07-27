# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery task that executes one agent session."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from apps.bus.channels import release_lock, try_acquire_lock
from apps.runner.loop import SessionRunner
from apps.runner.session_lifecycle import finalize_automated_trigger_session
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
)
from apps.sessions.services.commands import (
    create_activity,
    set_session_status,
    update_activity,
)
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _task_exit_details(fault: BaseException) -> tuple[str, str, str]:
    """Return stable lifecycle status, message, and code for a worker exit."""
    if isinstance(fault, asyncio.CancelledError):
        return AgentSessionActivityStatus.CANCELLED, 'Session cancelled', 'session_cancelled'
    if isinstance(fault, Exception):
        return AgentSessionActivityStatus.FAILED, 'Unexpected worker failure', 'unexpected_failure'
    return AgentSessionActivityStatus.FAILED, 'Session worker interrupted', 'session_interrupted'


def _terminalize_open_activities(
    session: AgentSession,
    *,
    status: str,
    message: str,
    code: str,
) -> None:
    """Close every open lifecycle before recording the task-boundary outcome."""
    open_ids = list(
        session.activities.filter(
            status__in={
                AgentSessionActivityStatus.PENDING,
                AgentSessionActivityStatus.RUNNING,
            }
        )
        .order_by('seq')
        .values_list('id', flat=True)
    )
    for activity_id in open_ids:
        update_activity(
            activity_id,
            status=status,
            summary=message,
            details={'message': message, 'code': code},
        )


@shared_task(bind=True, ignore_result=True)
def run_session(self: Any, session_id: str) -> None:
    """Execute one locked session and finalize only a normally returned run."""
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
        set_session_status(
            session,
            AgentSessionStatus.RUNNING,
            started_at=session.started_at or timezone.now(),
        )
    elif session.status in (AgentSessionStatus.WAITING, AgentSessionStatus.PAUSED):
        set_session_status(session, AgentSessionStatus.RUNNING)

    completed_normally = False
    try:
        SessionRunner.for_session(session, emit_restart=emit_restart).run()
        completed_normally = True
    except BaseException as fault:
        if isinstance(fault, Exception):
            logger.exception('Unhandled failure in session %s', session_id)
        else:
            logger.exception('Interrupted session %s', session_id)
        session.refresh_from_db()
        activity_status, message, code = _task_exit_details(fault)
        _terminalize_open_activities(
            session,
            status=activity_status,
            message=message,
            code=code,
        )
        create_activity(
            session,
            kind=AgentSessionActivityKind.FAILURE,
            status=AgentSessionActivityStatus.FAILED,
            name='failure',
            summary=message,
            details={'message': message, 'code': code},
        )
        set_session_status(session, AgentSessionStatus.WAITING)
        raise
    finally:
        if completed_normally:
            session.refresh_from_db()
            finalize_automated_trigger_session(session)
            session.refresh_from_db()
            if session.status == AgentSessionStatus.RUNNING:
                set_session_status(session, AgentSessionStatus.DONE, ended_at=timezone.now())
        release_lock(session_id, token)
