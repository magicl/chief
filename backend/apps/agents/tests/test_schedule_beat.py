# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for django-celery-beat sync of schedule triggers."""

from __future__ import annotations

import json
from unittest.mock import patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, AgentStatus, Trigger, TriggerStatus
from apps.agents.services.schedule_beat import (
    SCHEDULE_DISPATCH_TASK,
    periodic_task_name,
    sync_agent_schedule_triggers,
    sync_schedule_trigger,
)
from django.contrib.auth import get_user_model
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec
from libs.agent_spec.cron import CrontabFields, parse_cron_fields

from olib.py.django.test.cases import OTestCase


class TestParseCronFields(OTestCase):
    def test_splits_valid_five_field_cron(self) -> None:
        fields = parse_cron_fields('*/2 14 * * 1')

        self.assertEqual(
            fields,
            CrontabFields(
                minute='*/2',
                hour='14',
                day_of_month='*',
                month_of_year='*',
                day_of_week='1',
            ),
        )


class TestScheduleBeatSync(OTestCase):
    def _agent_with_schedule(self, *, cron: str = '0 * * * *') -> tuple[Agent, Trigger]:
        user = get_user_model().objects.create_user(username='beat-sync', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Beat', identifier='beat-sync-agent')
        with self.captureOnCommitCallbacks(execute=True):
            config = persist_agent_config(
                agent,
                AgentConfigSpec(
                    llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                    system_prompt='hello',
                    triggers=[
                        TriggerSpec(name='manual', kind='manual'),
                        TriggerSpec(
                            name='sweep',
                            kind='schedule',
                            cron=cron,
                            prompt='Run scheduled tasks.',
                        ),
                    ],
                ),
                source_rev='beat-sync-v1',
            )
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        return agent, trigger

    def test_persist_creates_enabled_periodic_task(self) -> None:
        _agent, trigger = self._agent_with_schedule()

        task = PeriodicTask.objects.get(name=periodic_task_name(trigger.id))

        self.assertTrue(task.enabled)
        self.assertEqual(task.task, SCHEDULE_DISPATCH_TASK)
        self.assertEqual(json.loads(task.args), [str(trigger.id)])
        assert task.crontab is not None
        self.assertEqual(task.crontab.minute, '0')
        self.assertEqual(task.crontab.hour, '*')
        self.assertEqual(str(task.crontab.timezone), 'UTC')

    def test_config_revision_disables_old_trigger_beat_task(self) -> None:
        agent, old_trigger = self._agent_with_schedule(cron='0 * * * *')
        old_task_name = periodic_task_name(old_trigger.id)

        with self.captureOnCommitCallbacks(execute=True):
            persist_agent_config(
                agent,
                AgentConfigSpec(
                    llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                    system_prompt='hello',
                    triggers=[
                        TriggerSpec(name='manual', kind='manual'),
                        TriggerSpec(
                            name='sweep',
                            kind='schedule',
                            cron='5 * * * *',
                            prompt='Run scheduled tasks.',
                        ),
                    ],
                ),
                source_rev='beat-sync-v2',
            )
        new_trigger = Trigger.objects.get(
            agent=agent,
            agent_config=agent.current_config,
            name='sweep',
        )

        old_task = PeriodicTask.objects.get(name=old_task_name)
        new_task = PeriodicTask.objects.get(name=periodic_task_name(new_trigger.id))

        self.assertFalse(old_task.enabled)
        self.assertTrue(new_task.enabled)
        self.assertEqual(new_task.crontab.minute, '5')

    def test_disabled_trigger_disables_periodic_task(self) -> None:
        _agent, trigger = self._agent_with_schedule()
        trigger.status = TriggerStatus.DISABLED
        trigger.save(update_fields=['status'])

        sync_schedule_trigger(trigger.id)

        task = PeriodicTask.objects.get(name=periodic_task_name(trigger.id))
        self.assertFalse(task.enabled)

    def test_disabled_agent_disables_periodic_task(self) -> None:
        agent, trigger = self._agent_with_schedule()
        agent.status = AgentStatus.DISABLED
        agent.save(update_fields=['status'])

        sync_schedule_trigger(trigger.id)

        task = PeriodicTask.objects.get(name=periodic_task_name(trigger.id))
        self.assertFalse(task.enabled)

    def test_agent_sync_checkpoints_surround_each_schedule_trigger(self) -> None:
        """Invoke generic maintenance around each trigger synchronization."""
        agent, trigger = self._agent_with_schedule()
        calls: list[str] = []

        def record_progress() -> None:
            """Record one generic maintenance checkpoint."""
            calls.append('progress')

        def record_trigger(_trigger: Trigger) -> None:
            """Record one current-trigger synchronization."""
            calls.append('trigger')

        with patch(
            'apps.agents.services.schedule_beat.upsert_schedule_trigger_beat',
            side_effect=record_trigger,
        ):
            sync_agent_schedule_triggers(agent.id, progress=record_progress)

        self.assertEqual(trigger.agent_id, agent.id)
        self.assertEqual(calls, ['progress', 'trigger', 'progress'])

    def test_reuses_existing_crontab_schedule_rows(self) -> None:
        before = CrontabSchedule.objects.count()
        self._agent_with_schedule(cron='0 9 * * *')
        user = get_user_model().objects.create_user(username='beat-sync-b', password='x')
        agent_b = Agent.objects.create(user_id=user.pk, name='Beat B', identifier='beat-sync-b-agent')
        with self.captureOnCommitCallbacks(execute=True):
            persist_agent_config(
                agent_b,
                AgentConfigSpec(
                    llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                    system_prompt='hello',
                    triggers=[
                        TriggerSpec(name='manual', kind='manual'),
                        TriggerSpec(
                            name='sweep',
                            kind='schedule',
                            cron='0 9 * * *',
                            prompt='Run scheduled tasks.',
                        ),
                    ],
                ),
                source_rev='beat-sync-b-v1',
            )

        self.assertEqual(CrontabSchedule.objects.count(), before + 1)

    def test_delete_trigger_disables_periodic_task(self) -> None:
        _agent, trigger = self._agent_with_schedule()
        task_name = periodic_task_name(trigger.id)

        with self.captureOnCommitCallbacks(execute=True):
            trigger.delete()

        task = PeriodicTask.objects.get(name=task_name)
        self.assertFalse(task.enabled)
