# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for schedule and queue trigger dispatch helpers."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, Trigger, TriggerKind, TriggerStatus
from apps.queues.models import Queue, QueueItem, QueueItemStatus
from apps.queues.services import commands
from apps.runner.scheduling import (
    SCHEDULE_BOOTSTRAP,
    _active_triggers,
    active_session_count,
    dispatch_queue_triggers,
    dispatch_queue_triggers_for_queue,
    dispatch_schedule_trigger,
    queue_item_bootstrap_message,
)
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model
from libs.agent_spec import AgentConfigSpec, LLMSpec, QueueSpec, TriggerSpec

from olib.py.django.test.cases import OTestCase


def _minimal_spec(*, triggers: list[TriggerSpec], queues: list[QueueSpec] | None = None) -> AgentConfigSpec:
    return AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        triggers=triggers,
        queues=queues or [],
    )


class TestActiveSessionCount(OTestCase):
    def test_counts_only_active_statuses_for_trigger(self) -> None:
        user = get_user_model().objects.create_user(username='sched-count', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Sched', identifier='sched-count-agent')
        spec = _minimal_spec(
            triggers=[
                TriggerSpec(name='manual', kind='manual'),
                TriggerSpec(name='sweep', kind='schedule', cron='0 * * * *'),
            ],
        )
        config = persist_agent_config(agent, spec, source_rev='sched-count-v1')
        schedule_trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        manual_trigger = Trigger.objects.get(agent=agent, agent_config=config, name='manual')

        for status in (
            AgentSessionStatus.QUEUED,
            AgentSessionStatus.RUNNING,
            AgentSessionStatus.PAUSED,
            AgentSessionStatus.WAITING,
        ):
            AgentSession.objects.create(
                agent=agent,
                agent_config=config,
                status=status,
                trigger_type=TriggerType.TRIGGER,
                trigger_ref=schedule_trigger.id,
            )

        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=schedule_trigger.id,
        )
        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=manual_trigger.id,
        )

        self.assertEqual(active_session_count(schedule_trigger), 4)


class TestQueueItemBootstrapMessage(OTestCase):
    def test_message_contains_item_id_and_payload_json(self) -> None:
        item_id = uuid.UUID('01234567-89ab-cdef-0123-456789abcdef')
        payload = {'subject': 'hello', 'priority': 2}

        message = queue_item_bootstrap_message(item_id=item_id, payload=payload)

        self.assertIn('Process this queue item.', message)
        self.assertIn(f'item_id: {item_id}', message)
        self.assertIn('payload:', message)
        self.assertIn(json.dumps(payload, indent=2, sort_keys=True), message)


class TestActiveTriggers(OTestCase):
    def test_filters_by_kind_status_and_current_config(self) -> None:
        user = get_user_model().objects.create_user(username='sched-triggers', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Sched', identifier='sched-triggers-agent')

        v1 = persist_agent_config(
            agent,
            _minimal_spec(
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(name='sweep', kind='schedule', cron='0 * * * *'),
                ],
            ),
            source_rev='sched-triggers-v1',
        )
        v1_schedule = Trigger.objects.get(agent=agent, agent_config=v1, name='sweep')

        v2 = persist_agent_config(
            agent,
            _minimal_spec(
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(name='sweep', kind='schedule', cron='5 * * * *'),
                    TriggerSpec(name='inbox', kind='queue', queue='inbox'),
                ],
                queues=[QueueSpec(id='inbox')],
            ),
            source_rev='sched-triggers-v2',
        )
        v2_schedule = Trigger.objects.get(agent=agent, agent_config=v2, name='sweep')
        v2_queue = Trigger.objects.get(agent=agent, agent_config=v2, name='inbox')

        v2_schedule.status = TriggerStatus.DISABLED
        v2_schedule.save(update_fields=['status'])

        schedule_triggers = _active_triggers(kind=TriggerKind.SCHEDULE)
        queue_triggers = _active_triggers(kind=TriggerKind.QUEUE)

        self.assertEqual(schedule_triggers, [])
        self.assertEqual(queue_triggers, [v2_queue])
        self.assertNotIn(v1_schedule, schedule_triggers)
        self.assertNotIn(v2_schedule, schedule_triggers)
        self.assertEqual(SCHEDULE_BOOTSTRAP, 'Scheduled run started. Execute your configured tasks.')


class TestDispatchScheduleTriggers(OTestCase):
    def _schedule_agent(self) -> tuple[Agent, Trigger]:
        user = get_user_model().objects.create_user(username='sched-dispatch', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Sched', identifier='sched-dispatch-agent')
        config = persist_agent_config(
            agent,
            _minimal_spec(
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(name='sweep', kind='schedule', cron='0 * * * *'),
                ],
            ),
            source_rev='sched-dispatch-v1',
        )
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        return agent, trigger

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_dispatch_creates_session_and_sets_last_fired_at(self, mock_push: MagicMock) -> None:
        agent, trigger = self._schedule_agent()
        fire_at = datetime(2026, 7, 5, 14, 0, tzinfo=UTC)

        started = dispatch_schedule_trigger(trigger_id=trigger.id, now=fire_at)

        self.assertTrue(started)
        self.assertEqual(AgentSession.objects.filter(agent=agent).count(), 1)
        session = AgentSession.objects.get(agent=agent)
        self.assertEqual(session.trigger_ref, trigger.id)
        mock_push.assert_called_once_with(session.id, SCHEDULE_BOOTSTRAP)
        trigger.refresh_from_db()
        self.assertEqual(trigger.last_fired_at, fire_at)

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_second_dispatch_at_capacity_does_not_start_another_session(self, mock_push: MagicMock) -> None:
        agent, trigger = self._schedule_agent()
        fire_at = datetime(2026, 7, 5, 14, 0, tzinfo=UTC)

        first = dispatch_schedule_trigger(trigger_id=trigger.id, now=fire_at)
        second = dispatch_schedule_trigger(
            trigger_id=trigger.id,
            now=fire_at.replace(second=30),
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(AgentSession.objects.filter(agent=agent).count(), 1)
        mock_push.assert_called_once()
        trigger.refresh_from_db()
        self.assertEqual(trigger.last_fired_at, fire_at.replace(second=30))

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_max_sessions_capacity_skips_session_but_updates_last_fired_at(
        self, mock_push: MagicMock
    ) -> None:
        agent, trigger = self._schedule_agent()
        config = agent.current_config
        assert config is not None
        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id,
        )
        fire_at = datetime(2026, 7, 5, 14, 0, tzinfo=UTC)

        started = dispatch_schedule_trigger(trigger_id=trigger.id, now=fire_at)

        self.assertFalse(started)
        self.assertEqual(AgentSession.objects.filter(agent=agent).count(), 1)
        mock_push.assert_not_called()
        trigger.refresh_from_db()
        self.assertEqual(trigger.last_fired_at, fire_at)

    @patch('apps.runner.scheduling.logger')
    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    @patch('apps.runner.session_start.start_trigger_session')
    def test_one_trigger_failure_does_not_block_others(
        self,
        mock_start: MagicMock,
        mock_push: MagicMock,
        mock_logger: MagicMock,
    ) -> None:
        user = get_user_model().objects.create_user(username='sched-isolate', password='x')
        agent_a = Agent.objects.create(user_id=user.pk, name='A', identifier='sched-isolate-a')
        agent_b = Agent.objects.create(user_id=user.pk, name='B', identifier='sched-isolate-b')
        for agent, rev in ((agent_a, 'sched-isolate-a-v1'), (agent_b, 'sched-isolate-b-v1')):
            persist_agent_config(
                agent,
                _minimal_spec(
                    triggers=[
                        TriggerSpec(name='manual', kind='manual'),
                        TriggerSpec(name='sweep', kind='schedule', cron='0 * * * *'),
                    ],
                ),
                source_rev=rev,
            )

        trigger_b = Trigger.objects.get(agent=agent_b, name='sweep')
        trigger_a = Trigger.objects.get(agent=agent_a, name='sweep')

        def start_side_effect(agent: Agent, trigger: Trigger) -> AgentSession:
            if trigger.agent_id == agent_a.pk:
                raise RuntimeError('simulated schedule failure')
            config = agent.current_config
            assert config is not None
            return AgentSession.objects.create(
                agent=agent,
                agent_config=config,
                status=AgentSessionStatus.QUEUED,
                trigger_type=TriggerType.TRIGGER,
                trigger_ref=trigger.id,
            )

        mock_start.side_effect = start_side_effect
        fire_at = datetime(2026, 7, 5, 14, 0, tzinfo=UTC)

        dispatch_schedule_trigger(trigger_id=trigger_a.id, now=fire_at)
        started_b = dispatch_schedule_trigger(trigger_id=trigger_b.id, now=fire_at)

        self.assertTrue(started_b)
        session_b = AgentSession.objects.get(agent=agent_b, trigger_ref=trigger_b.id)
        mock_push.assert_called_once_with(session_b.id, SCHEDULE_BOOTSTRAP)


class TestDispatchQueueTriggers(OTestCase):
    def _queue_agent(
        self,
        *,
        username: str,
        identifier: str,
        max_sessions: int = 1,
        queue_id: str = 'inbox',
    ) -> tuple[Agent, Trigger, Queue]:
        user = get_user_model().objects.create_user(username=username, password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Queue', identifier=identifier)
        config = persist_agent_config(
            agent,
            _minimal_spec(
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(
                        name='worker',
                        kind='queue',
                        queue=queue_id,
                        max_sessions=max_sessions,
                    ),
                ],
                queues=[QueueSpec(id=queue_id)],
            ),
            source_rev=f'{identifier}-v1',
        )
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='worker')
        queue = Queue.objects.get(agent=agent, queue_id=queue_id)
        return agent, trigger, queue

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_put_item_then_dispatch_takes_item_and_bootstraps(self, mock_push: MagicMock) -> None:
        agent, trigger, queue = self._queue_agent(
            username='queue-dispatch',
            identifier='queue-dispatch-agent',
        )
        put_result = commands.put_item(queue=queue, payload={'subject': 'hello'})

        stats = dispatch_queue_triggers()

        self.assertEqual(stats.queue_sessions, 1)
        session = AgentSession.objects.get(agent=agent, trigger_ref=trigger.id)
        item = QueueItem.objects.get(pk=put_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.TAKEN)
        self.assertEqual(item.taken_by_session_id, session.id)
        mock_push.assert_called_once()
        session_id, message = mock_push.call_args.args
        self.assertEqual(session_id, session.id)
        self.assertIn(f'item_id: {put_result.item_id}', message)
        self.assertIn(json.dumps({'subject': 'hello'}, indent=2, sort_keys=True), message)

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_max_sessions_capacity_skips_second_dispatch(self, mock_push: MagicMock) -> None:
        agent, trigger, queue = self._queue_agent(
            username='queue-max',
            identifier='queue-max-agent',
            max_sessions=1,
        )
        config = agent.current_config
        assert config is not None
        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id,
        )
        commands.put_item(queue=queue, payload={'first': 1})
        commands.put_item(queue=queue, payload={'second': 2})

        stats = dispatch_queue_triggers()

        self.assertEqual(stats.queue_sessions, 0)
        self.assertEqual(AgentSession.objects.filter(agent=agent, trigger_ref=trigger.id).count(), 1)
        self.assertEqual(QueueItem.objects.filter(queue=queue, status=QueueItemStatus.AVAILABLE).count(), 2)
        mock_push.assert_not_called()

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_dispatch_for_queue_only_runs_matching_triggers(self, mock_push: MagicMock) -> None:
        agent_a, trigger_a, queue_a = self._queue_agent(
            username='queue-scope-a',
            identifier='queue-scope-a-agent',
            queue_id='inbox-a',
        )
        agent_b, trigger_b, queue_b = self._queue_agent(
            username='queue-scope-b',
            identifier='queue-scope-b-agent',
            queue_id='inbox-b',
        )
        put_a = commands.put_item(queue=queue_a, payload={'agent': 'a'})
        commands.put_item(queue=queue_b, payload={'agent': 'b'})

        stats = dispatch_queue_triggers_for_queue(queue_pk=str(queue_a.pk))

        self.assertEqual(stats.queue_sessions, 1)
        self.assertEqual(AgentSession.objects.filter(agent=agent_a, trigger_ref=trigger_a.id).count(), 1)
        self.assertEqual(AgentSession.objects.filter(agent=agent_b, trigger_ref=trigger_b.id).count(), 0)
        item_a = QueueItem.objects.get(pk=put_a.item_id)
        self.assertEqual(item_a.status, QueueItemStatus.TAKEN)
        self.assertEqual(
            QueueItem.objects.filter(queue=queue_b, status=QueueItemStatus.AVAILABLE).count(),
            1,
        )
        mock_push.assert_called_once()
