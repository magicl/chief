# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Sync django-celery-beat PeriodicTask rows for schedule triggers."""

from __future__ import annotations

import json
import logging
from uuid import UUID

from apps.agents.models import Agent, Trigger, TriggerKind, TriggerStatus
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from libs.agent_spec.cron import parse_cron_fields

logger = logging.getLogger(__name__)

SCHEDULE_DISPATCH_TASK = 'apps.runner.trigger_tasks.dispatch_schedule_trigger'
PERIODIC_TASK_PREFIX = 'chief:schedule-trigger:'


def periodic_task_name(trigger_id: UUID) -> str:
    """Return the stable PeriodicTask name for a schedule trigger row."""
    return f'{PERIODIC_TASK_PREFIX}{trigger_id}'


def _crontab_for_expression(cron: str) -> CrontabSchedule:
    """Return a shared ``CrontabSchedule`` row for a validated 5-field cron (UTC)."""
    fields = parse_cron_fields(cron)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=fields.minute,
        hour=fields.hour,
        day_of_month=fields.day_of_month,
        month_of_year=fields.month_of_year,
        day_of_week=fields.day_of_week,
        timezone='UTC',
    )
    return schedule


def disable_schedule_trigger_beat(trigger_id: UUID) -> None:
    """Disable beat dispatch for *trigger_id* without deleting the PeriodicTask row."""
    PeriodicTask.objects.filter(name=periodic_task_name(trigger_id)).update(enabled=False)


def upsert_schedule_trigger_beat(trigger: Trigger) -> None:
    """Create or update the PeriodicTask that fires *trigger* on its cron schedule."""
    cron = trigger.spec.get('cron')
    if not cron:
        logger.warning('schedule trigger %s has no cron in spec; disabling beat task', trigger.pk)
        disable_schedule_trigger_beat(trigger.id)
        return

    crontab = _crontab_for_expression(str(cron))
    PeriodicTask.objects.update_or_create(
        name=periodic_task_name(trigger.id),
        defaults={
            'task': SCHEDULE_DISPATCH_TASK,
            'crontab': crontab,
            'args': json.dumps([str(trigger.id)]),
            'kwargs': json.dumps({}),
            'enabled': True,
            'description': f'Schedule trigger {trigger.agent.identifier}:{trigger.name}',
        },
    )


def sync_schedule_trigger(trigger_id: UUID) -> None:
    """Sync one schedule trigger's beat row from DB state (current config + status)."""
    try:
        trigger = Trigger.objects.select_related('agent').get(pk=trigger_id)
    except Trigger.DoesNotExist:
        disable_schedule_trigger_beat(trigger_id)
        return

    if trigger.kind != TriggerKind.SCHEDULE:
        return

    agent = trigger.agent
    if trigger.agent_config_id != agent.current_config_id:
        disable_schedule_trigger_beat(trigger.id)
        return

    if trigger.status == TriggerStatus.ACTIVE:
        upsert_schedule_trigger_beat(trigger)
    else:
        disable_schedule_trigger_beat(trigger.id)


def sync_agent_schedule_triggers(agent_id: UUID) -> None:
    """Rebuild beat tasks for all schedule triggers on *agent*'s current config."""
    try:
        agent = Agent.objects.get(pk=agent_id)
    except Agent.DoesNotExist:
        return

    stale_ids = Trigger.objects.filter(
        agent_id=agent_id,
        kind=TriggerKind.SCHEDULE,
    ).exclude(agent_config_id=agent.current_config_id).values_list('id', flat=True)
    for trigger_id in stale_ids:
        disable_schedule_trigger_beat(trigger_id)

    if agent.current_config_id is None:
        return

    for trigger in Trigger.objects.filter(
        agent_id=agent_id,
        agent_config_id=agent.current_config_id,
        kind=TriggerKind.SCHEDULE,
    ):
        if trigger.status == TriggerStatus.ACTIVE:
            upsert_schedule_trigger_beat(trigger)
        else:
            disable_schedule_trigger_beat(trigger.id)


def sync_all_schedule_triggers() -> None:
    """Rebuild PeriodicTask rows for every agent's current schedule triggers."""
    agent_ids = Agent.objects.filter(current_config_id__isnull=False).values_list('id', flat=True)
    for agent_id in agent_ids:
        sync_agent_schedule_triggers(agent_id)
