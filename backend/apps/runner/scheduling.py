# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Schedule and queue trigger dispatch helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from apps.agents.models import AgentStatus, Trigger, TriggerKind, TriggerStatus
from apps.queues.models import Queue
from apps.sessions.models import AgentSession, AgentSessionStatus
from django.db import transaction
from django.db.models import F
from libs.agent_spec.trigger_prompts import default_trigger_prompt

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset(
    {
        AgentSessionStatus.QUEUED,
        AgentSessionStatus.RUNNING,
        AgentSessionStatus.PAUSED,
        AgentSessionStatus.WAITING,
    }
)


@dataclass
class DispatchStats:
    """Counts of sessions started by a dispatch pass."""

    schedule_sessions: int = 0
    queue_sessions: int = 0


def active_session_count(trigger: Trigger) -> int:
    """Return in-flight sessions bound to *trigger* (queued through waiting)."""
    statuses = set(_ACTIVE_STATUSES)
    # Automated triggers finalize at waiting; stale waiting rows must not block capacity.
    if trigger.kind in (TriggerKind.SCHEDULE, TriggerKind.QUEUE):
        statuses.discard(AgentSessionStatus.WAITING)
    return AgentSession.objects.filter(
        trigger_ref=trigger.id,
        status__in=statuses,
    ).count()


def trigger_max_sessions(trigger: Trigger) -> int | None:
    """Return configured concurrency cap, or ``None`` when unlimited."""
    if 'max_sessions' not in trigger.spec:
        if trigger.kind in (TriggerKind.SCHEDULE, TriggerKind.QUEUE):
            return 1
        return None
    raw = trigger.spec.get('max_sessions')
    if raw is None:
        return None
    return int(raw)


def trigger_has_capacity(trigger: Trigger) -> bool:
    """True when *trigger* may start another session under its ``max_sessions`` cap."""
    cap = trigger_max_sessions(trigger)
    if cap is None:
        return True
    return active_session_count(trigger) < cap


def trigger_prompt(trigger: Trigger) -> str:
    """Return the configured bootstrap prompt for *trigger*, with legacy defaults as fallback."""
    raw = trigger.spec.get('prompt')
    if raw and str(raw).strip():
        return str(raw).strip()
    fallback = default_trigger_prompt(trigger.kind)
    return fallback or ''


def queue_item_bootstrap_message(*, prompt: str, item_id: UUID, payload: dict[str, object]) -> str:
    """Format the bootstrap user message for a queue-trigger session."""
    payload_json = json.dumps(payload, indent=2, sort_keys=True)
    return f'{prompt.rstrip()}\n' '\n' f'item_id: {item_id}\n' '\n' f'payload:\n{payload_json}'


def _active_triggers(*, kind: str) -> list[Trigger]:
    """Return active triggers of *kind* on active agents' current config revisions."""
    return list(
        Trigger.objects.filter(
            kind=kind,
            status=TriggerStatus.ACTIVE,
            agent__status=AgentStatus.ACTIVE,
            agent__current_config_id=F('agent_config_id'),
        ).select_related('agent', 'agent_config')
    )


def dispatch_schedule_trigger(*, trigger_id: UUID | str, now: datetime | None = None) -> bool:
    """Start a session when an active agent's schedule trigger beat task fires."""
    from apps.agents.services.schedule_beat import disable_schedule_trigger_beat
    from apps.runner.dispatch import push_chat_and_dispatch
    from apps.runner.session_start import start_trigger_session
    from django.utils import timezone

    if now is None:
        now = timezone.now()

    try:
        trigger = Trigger.objects.select_related('agent').get(pk=trigger_id)
    except Trigger.DoesNotExist:
        logger.warning('dispatch_schedule_trigger: trigger %s not found', trigger_id)
        return False

    agent = trigger.agent
    if trigger.kind != TriggerKind.SCHEDULE:
        logger.warning('dispatch_schedule_trigger: trigger %s is not schedule kind', trigger.pk)
        return False

    if (
        agent.status != AgentStatus.ACTIVE
        or trigger.agent_config_id != agent.current_config_id
        or trigger.status != TriggerStatus.ACTIVE
    ):
        disable_schedule_trigger_beat(trigger.id)
        return False

    session = None
    try:
        with transaction.atomic():
            locked = Trigger.objects.select_for_update().get(pk=trigger.pk)
            if trigger_has_capacity(locked):
                session = start_trigger_session(locked.agent, locked)
            Trigger.objects.filter(pk=trigger.pk).update(last_fired_at=now)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception('schedule dispatch failed for trigger %s', trigger.pk)
        return False

    if session is not None:
        push_chat_and_dispatch(session.id, trigger_prompt(trigger))
        return True
    return False


def _resolve_queue_for_trigger(trigger: Trigger) -> Queue | None:
    """Return the ``Queue`` row for *trigger*'s ``queue`` spec id, or ``None`` if missing."""
    queue_id = trigger.spec.get('queue')
    if not queue_id:
        logger.warning('queue trigger %s has no queue id in spec', trigger.pk)
        return None
    try:
        return Queue.objects.get(agent=trigger.agent, queue_id=queue_id)
    except Queue.DoesNotExist:
        logger.warning(
            'queue trigger %s references missing queue %r for agent %s',
            trigger.pk,
            queue_id,
            trigger.agent_id,
        )
        return None


def _fill_queue_trigger_slots(trigger: Trigger, queue: Queue) -> int:
    """Start sessions and take items while *trigger* has free ``max_sessions`` slots."""
    from apps.queues.services.commands import take_item
    from apps.runner.dispatch import push_chat_and_dispatch
    from apps.runner.session_start import StartSessionError, start_trigger_session

    started = 0

    while True:
        take_result = None
        session = None
        try:
            with transaction.atomic():
                Trigger.objects.select_for_update().get(pk=trigger.pk)
                if not trigger_has_capacity(trigger):
                    break
                session = start_trigger_session(trigger.agent, trigger)
                take_result = take_item(queue=queue, session_id=session.id)
                if take_result is None:
                    session.delete()
                    session = None
                    break
        except StartSessionError as exc:
            logger.info('queue dispatch skipped trigger %s: %s', trigger.pk, exc)
            break
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception('queue dispatch failed for trigger %s', trigger.pk)
            break

        if session is None or take_result is None:
            break

        message = queue_item_bootstrap_message(
            prompt=trigger_prompt(trigger),
            item_id=take_result.item_id,
            payload=take_result.payload,
        )
        push_chat_and_dispatch(session.id, message)
        started += 1

    return started


def dispatch_queue_triggers() -> DispatchStats:
    """Fill queue trigger slots for all active queue triggers."""
    stats = DispatchStats()

    for trigger in _active_triggers(kind=TriggerKind.QUEUE):
        queue = _resolve_queue_for_trigger(trigger)
        if queue is None:
            continue
        stats.queue_sessions += _fill_queue_trigger_slots(trigger, queue)

    return stats


def dispatch_queue_triggers_for_queue(*, queue_pk: str) -> DispatchStats:
    """Fill active-agent trigger slots bound to the queue identified by *queue_pk*."""
    try:
        queue = Queue.objects.select_related('agent').get(pk=queue_pk)
    except Queue.DoesNotExist:
        logger.warning('dispatch_queue_triggers_for_queue: queue %s not found', queue_pk)
        return DispatchStats()

    stats = DispatchStats()
    queue_id = queue.queue_id

    triggers = Trigger.objects.filter(
        kind=TriggerKind.QUEUE,
        status=TriggerStatus.ACTIVE,
        agent_id=queue.agent_id,
        agent__status=AgentStatus.ACTIVE,
        agent__current_config_id=F('agent_config_id'),
        spec__queue=queue_id,
    ).select_related('agent', 'agent_config')

    for trigger in triggers:
        stats.queue_sessions += _fill_queue_trigger_slots(trigger, queue)

    return stats
