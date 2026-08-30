# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session domain mutations."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from apps.agents.models import Agent, AgentStatus, Trigger, TriggerStatus
from apps.sessions.activities import (
    ACTIVITY_UPDATE_UNSET,
    TERMINAL_ACTIVITY_STATUSES,
    ActivityUpdateUnset,
    create_activity_row,
    update_activity_row,
)
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
    TriggerType,
)
from apps.sessions.notify import publish_session_activity, publish_session_update
from apps.sessions.services.queries import input_activity_count
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from libs.algorithms import DEFAULT_CHAT_NAME_CONFIG, get_algorithm

logger = logging.getLogger(__name__)

# Session statuses intentionally remain the production five-state machine:
# queued is pending, running/paused/ordinary waiting are active, and done is
# successful. A latest failure activity overrides waiting or automated done.
_CHILD_STATUS_TO_ACTIVITY: dict[str, str] = {
    AgentSessionStatus.QUEUED: AgentSessionActivityStatus.PENDING,
    AgentSessionStatus.RUNNING: AgentSessionActivityStatus.RUNNING,
    AgentSessionStatus.WAITING: AgentSessionActivityStatus.RUNNING,
    AgentSessionStatus.PAUSED: AgentSessionActivityStatus.RUNNING,
    AgentSessionStatus.DONE: AgentSessionActivityStatus.SUCCEEDED,
}
_ACTIVE_ACTIVITY_STATUSES = frozenset(
    {
        AgentSessionActivityStatus.PENDING,
        AgentSessionActivityStatus.RUNNING,
    }
)


def _publish_activity_after_commit(activity: AgentSessionActivity) -> None:
    """Schedule one best-effort upsert for this exact persisted revision."""
    session_id = activity.session_id
    # Freeze nested JSON now: callers may mutate the returned model while an
    # outer transaction delays the on_commit callback.
    payload = copy.deepcopy(activity.to_stream_dict())

    def publish() -> None:
        """Keep Redis transport failure independent from committed activity state."""
        try:
            publish_session_activity(session_id, payload)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug('Session activity transport unavailable')

    transaction.on_commit(publish, robust=True)


def create_activity(
    session: AgentSession,
    *,
    kind: str,
    status: str,
    name: str,
    summary: str,
    details: dict[str, Any],
    parent_id: UUID | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    child_session_id: UUID | None = None,
) -> AgentSessionActivity:
    """Persist an activity and publish its full state after commit."""
    activity = create_activity_row(
        session,
        kind=kind,
        status=status,
        name=name,
        summary=summary,
        details=copy.deepcopy(details),
        parent_id=parent_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        started_at=started_at,
        ended_at=ended_at,
        child_session_id=child_session_id,
    )
    _publish_activity_after_commit(activity)
    return activity


def update_activity(
    activity_id: UUID,
    *,
    status: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    model: str | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    input_tokens: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    output_tokens: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    cost_usd: Decimal | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    latency_ms: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    started_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    ended_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
) -> AgentSessionActivity:
    """Apply one locked revision without exposing immutable child linkage."""
    activity = update_activity_row(
        activity_id,
        status=status,
        summary=summary,
        details=copy.deepcopy(details) if details is not None else None,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        started_at=started_at,
        ended_at=ended_at,
    )
    _publish_activity_after_commit(activity)
    return activity


def _validate_linked_start(
    *,
    parent_session: AgentSession,
    agent: Agent,
    trigger: Trigger | None,
) -> tuple[AgentSession, Agent]:
    """Lock and validate the parent plus child runtime configuration."""
    try:
        locked_parent = AgentSession.objects.select_for_update().select_related('agent').get(pk=parent_session.pk)
    except AgentSession.DoesNotExist as exc:
        raise ValidationError({'parent_session': 'parent session does not exist'}) from exc
    try:
        locked_agent = Agent.objects.select_for_update().select_related('current_config').get(pk=agent.pk)
    except Agent.DoesNotExist as exc:
        raise ValidationError({'agent': 'child agent does not exist'}) from exc

    if locked_parent.agent.user_id != locked_agent.user_id:
        raise ValidationError({'agent': 'parent session and child agent must have the same owner'})
    if locked_agent.status != AgentStatus.ACTIVE:
        raise ValidationError({'agent': 'child agent is disabled'})
    if locked_agent.current_config_id is None:
        raise ValidationError({'agent': 'child agent has no current config'})

    if trigger is not None:
        try:
            locked_trigger = Trigger.objects.select_for_update().get(pk=trigger.pk)
        except Trigger.DoesNotExist as exc:
            raise ValidationError({'trigger': 'trigger does not exist'}) from exc
        if locked_trigger.agent_id != locked_agent.id:
            raise ValidationError({'trigger': 'trigger must belong to the child agent'})
        if locked_trigger.agent_config_id != locked_agent.current_config_id:
            raise ValidationError({'trigger': 'trigger must use the child agent current config'})
        if locked_trigger.status != TriggerStatus.ACTIVE:
            raise ValidationError({'trigger': 'trigger must be active'})
    return locked_parent, locked_agent


def _assert_no_ancestry_cycle(*, child_id: UUID, new_parent: AgentSession) -> None:
    """Lock the prospective ancestor chain and reject repeated or child identities."""
    seen = {child_id}
    current_id: UUID | None = new_parent.id
    while current_id is not None:
        if current_id in seen:
            raise ValidationError({'parent_session': 'session ancestry cannot contain a cycle'})
        seen.add(current_id)
        current = AgentSession.objects.select_for_update().only('parent_session_id').get(pk=current_id)
        current_id = current.parent_session_id


@transaction.atomic
def start_linked_child_session(
    *,
    parent_session: AgentSession,
    agent: Agent,
    parent_activity_id: UUID | None = None,
    trigger: Trigger | None = None,
    name: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    dispatch: bool = True,
    dispatch_callback: Callable[[UUID], bool] | None = None,
) -> AgentSession:
    """Atomically create, reference, and optionally dispatch one child session."""
    if dispatch and dispatch_callback is None:
        raise ValidationError({'dispatch': 'linked child dispatch callback is required'})

    locked_parent, locked_agent = _validate_linked_start(
        parent_session=parent_session,
        agent=agent,
        trigger=trigger,
    )
    parent_activity = None
    if parent_activity_id is not None:
        try:
            parent_activity = AgentSessionActivity.objects.select_for_update().get(pk=parent_activity_id)
        except AgentSessionActivity.DoesNotExist as exc:
            raise ValidationError({'parent_activity_id': 'parent activity does not exist'}) from exc
        if parent_activity.session_id != locked_parent.id:
            raise ValidationError({'parent_activity_id': 'parent activity must belong to the parent session'})

    child = AgentSession(
        agent=locked_agent,
        agent_config=locked_agent.current_config,
        parent_session=locked_parent,
        status=AgentSessionStatus.QUEUED,
        trigger_type=TriggerType.TOOL_CALL,
        trigger_ref=trigger.id if trigger is not None else None,
    )
    _assert_no_ancestry_cycle(child_id=child.id, new_parent=locked_parent)
    child.save()

    activity_details = copy.deepcopy(details or {})
    activity_details['child_status'] = AgentSessionStatus.QUEUED
    create_activity(
        locked_parent,
        kind=AgentSessionActivityKind.SUBAGENT,
        status=AgentSessionActivityStatus.PENDING,
        name=name or locked_agent.identifier,
        summary=summary or f'{locked_agent.name}: queued',
        details=activity_details,
        parent_id=parent_activity.id if parent_activity is not None else None,
        child_session_id=child.id,
    )

    if dispatch and dispatch_callback is not None:
        child_id = child.id
        transaction.on_commit(lambda: _dispatch_linked_child(child_id, dispatch_callback))
    return child


def _dispatch_linked_child(child_id: UUID, dispatch_callback: Callable[[UUID], bool]) -> None:
    """Dispatch a frozen child identity and durably reconcile startup refusal."""
    try:
        dispatched = dispatch_callback(child_id)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.info('Linked child dispatch did not complete for session %s; recording startup outcome', child_id)
        dispatched = False
    if dispatched:
        return
    _record_child_startup_failure(child_id)


@transaction.atomic
def _record_child_startup_failure(child_id: UUID) -> None:
    """Persist one stable startup failure and reconcile the parent reference."""
    try:
        _, child = _lock_child_reference_then_session(child_id)
    except AgentSession.DoesNotExist:
        return
    create_activity(
        child,
        kind=AgentSessionActivityKind.FAILURE,
        status=AgentSessionActivityStatus.FAILED,
        name='failure',
        summary='Child startup failed',
        details={'code': 'child_dispatch_failed', 'message': 'Child session could not be dispatched'},
    )
    set_session_status(child, AgentSessionStatus.WAITING)


def _child_reference_state(
    child: AgentSession,
) -> tuple[str, str, dict[str, Any]]:
    """Derive parent lifecycle from real child status and its latest outcome."""
    activity_status = _CHILD_STATUS_TO_ACTIVITY.get(child.status)
    if activity_status is None:
        raise ValidationError({'status': 'child session status cannot be reconciled'})

    details: dict[str, Any] = {'child_status': child.status}
    summary = f'{child.agent.name}: {child.status}'
    if child.status in (AgentSessionStatus.WAITING, AgentSessionStatus.DONE):
        latest = (
            child.activities.order_by('-seq').only('id', 'session_id', 'kind', 'status', 'summary', 'details').first()
        )
        if (
            latest is not None
            and latest.kind == AgentSessionActivityKind.FAILURE
            and latest.status == AgentSessionActivityStatus.FAILED
        ):
            activity_status = AgentSessionActivityStatus.FAILED
            summary = latest.summary
            failure_code = latest.details.get('code') if isinstance(latest.details, dict) else None
            if isinstance(failure_code, str):
                details['failure_code'] = failure_code
    return activity_status, summary, details


def _apply_subagent_reconciliation(
    reference: AgentSessionActivity,
    *,
    status: str,
    summary: str,
    details: dict[str, Any],
) -> AgentSessionActivity:
    """Revise only an authoritative locked subagent reference."""
    if reference.status in TERMINAL_ACTIVITY_STATUSES and status in _ACTIVE_ACTIVITY_STATUSES:
        return reference
    merged_details = copy.deepcopy(reference.details or {})
    merged_details.pop('failure_code', None)
    merged_details.update(details)
    if reference.status == status and reference.summary == summary and reference.details == merged_details:
        return reference
    reference.status = status
    reference.summary = summary
    reference.details = merged_details
    if status in TERMINAL_ACTIVITY_STATUSES and reference.ended_at is None:
        reference.ended_at = timezone.now()
    reference.revision += 1
    reference.save(update_fields=['status', 'summary', 'details', 'ended_at', 'revision'])
    _publish_activity_after_commit(reference)
    return reference


def _lock_child_reference_then_session(
    child_id: UUID,
) -> tuple[AgentSessionActivity | None, AgentSession]:
    """Acquire the canonical parent-reference then child-session lock order."""
    reference = (
        AgentSessionActivity.objects.select_for_update()
        .select_related('session__agent')
        .filter(child_session_id=child_id)
        .first()
    )
    child = AgentSession.objects.select_for_update().select_related('agent').get(pk=child_id)
    if reference is None:
        return None, child
    if reference.kind != AgentSessionActivityKind.SUBAGENT or child.parent_session_id != reference.session_id:
        raise ValidationError({'child_session': 'child reference does not match its parent session'})
    if reference.session.agent.user_id != child.agent.user_id:
        raise ValidationError({'child_session': 'child and parent session must have the same owner'})
    return reference, child


@transaction.atomic
def reconcile_subagent_activity(child_session: AgentSession) -> AgentSessionActivity | None:
    """Authoritatively reconcile one exact same-owner child reference."""
    if child_session.parent_session_id is None:
        return None
    try:
        reference, child = _lock_child_reference_then_session(child_session.pk)
    except AgentSession.DoesNotExist:
        return None
    if reference is None:
        return None
    activity_status, summary, details = _child_reference_state(child)
    return _apply_subagent_reconciliation(
        reference,
        status=activity_status,
        summary=summary,
        details=details,
    )


@transaction.atomic
def set_session_status(
    session: AgentSession,
    status: str,
    *,
    started_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    ended_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
) -> AgentSession:
    """Persist a real session status and reconcile its parent in one transaction."""
    if status not in AgentSessionStatus.values:
        raise ValidationError({'status': 'invalid session status'})
    reference = None
    if session.parent_session_id is None:
        locked = AgentSession.objects.select_for_update().select_related('agent').get(pk=session.pk)
    else:
        reference, locked = _lock_child_reference_then_session(session.pk)
    status_changed = locked.status != status
    locked.status = status
    update_fields = ['status']
    if not isinstance(started_at, ActivityUpdateUnset):
        locked.started_at = started_at
        update_fields.append('started_at')
    if not isinstance(ended_at, ActivityUpdateUnset):
        locked.ended_at = ended_at
        update_fields.append('ended_at')
    if status_changed or len(update_fields) > 1:
        locked.save(update_fields=update_fields)
    session.status = locked.status
    session.started_at = locked.started_at
    session.ended_at = locked.ended_at
    if status_changed and reference is not None:
        activity_status, summary, details = _child_reference_state(locked)
        _apply_subagent_reconciliation(
            reference,
            status=activity_status,
            summary=summary,
            details=details,
        )
    return locked


@transaction.atomic
def reconcile_deleted_subagent_reference(
    activity_id: UUID,
    *,
    prior_child_session_id: UUID,
) -> AgentSessionActivity | None:
    """Finalize a directly deleted child's now-null parent reference."""
    try:
        reference = AgentSessionActivity.objects.select_for_update().get(pk=activity_id)
    except AgentSessionActivity.DoesNotExist:
        return None
    if reference.kind != AgentSessionActivityKind.SUBAGENT or reference.child_session_id is not None:
        return None
    details = copy.deepcopy(reference.details or {})
    details.update(
        {
            'child_status': 'unavailable',
            'failure_code': 'child_session_deleted',
            'prior_child_session_id': str(prior_child_session_id),
        }
    )
    return _apply_subagent_reconciliation(
        reference,
        status=AgentSessionActivityStatus.FAILED,
        summary='Child session unavailable',
        details=details,
    )


@transaction.atomic
def record_input(session: AgentSession, content: str) -> AgentSessionActivity:
    """Persist a terminal input activity and retain first-message name scheduling."""
    # create_activity locks the session row; this outer transaction retains that
    # lock through the count so concurrent inputs cannot both appear first.
    row = create_activity(
        session,
        kind=AgentSessionActivityKind.INPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='input',
        summary=(content[:120] + '…') if len(content) > 120 else content,
        details={'content': content},
    )
    if input_activity_count(session.id) == 1 and DEFAULT_CHAT_NAME_CONFIG.enabled:
        transaction.on_commit(lambda: _schedule_generate_session_name(session.id))
    return row


@transaction.atomic
def create_algorithm_session(*, user_id: int, algorithm_id: str) -> AgentSession:
    """Open a running session owned by a registered background algorithm.

    Algorithm sessions have no agent, config, or parent — they exist so a
    one-off job (a Celery task, not the runner) has somewhere to write its
    activity trace and spend. The caller performs the work inline; nothing is
    dispatched to ``run_session``.
    """
    if get_algorithm(algorithm_id) is None:
        raise ValidationError({'algorithm_id': 'algorithm must be registered'})
    return AgentSession.objects.create(
        user_id=user_id,
        algorithm_id=algorithm_id,
        status=AgentSessionStatus.RUNNING,
        trigger_type=TriggerType.ALGORITHM,
        started_at=timezone.now(),
    )


def finish_algorithm_session(session: AgentSession, *, name: str | None = None) -> AgentSession:
    """Name (when still unnamed) and close one algorithm session as done.

    A failed provider call still finishes ``done``: the failure is visible on
    the session's own llm activity, and the job itself ran to completion.
    """
    if name:
        update_session_name(session.id, name)
    return set_session_status(session, AgentSessionStatus.DONE, ended_at=timezone.now())


def update_session_name(session_id: UUID, name: str, *, source: str = 'auto') -> bool:
    """Set an unset session name and notify clients after normalization."""
    del source
    normalized = _normalize_name(name)
    if not normalized:
        return False
    updated = AgentSession.objects.filter(pk=session_id, name__isnull=True).update(name=normalized)
    if updated:
        publish_session_update(session_id, {'name': normalized})
    return bool(updated)


def _normalize_name(name: str, *, max_len: int = 80) -> str:
    """Collapse whitespace and constrain a generated session name."""
    text = ' '.join(name.split())
    if not text:
        return ''
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + '…'
    return text


def _schedule_generate_session_name(session_id: UUID) -> None:
    """Register chat-name generation after the surrounding transaction commits."""

    def enqueue() -> None:
        """Send the name-generation task without importing Celery at module load."""
        from celery import current_app

        current_app.send_task(
            'apps.sessions.tasks.generate_session_name',
            args=[str(session_id)],
        )

    transaction.on_commit(enqueue)
