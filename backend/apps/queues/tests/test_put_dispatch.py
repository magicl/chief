# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for immediate queue dispatch when items are put."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, Trigger
from apps.queues.models import Queue, QueueItemStatus
from apps.queues.services import commands
from apps.queues.tests.base import make_test_queue
from apps.runner.scheduling import dispatch_queue_triggers_for_queue
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model
from libs.agent_spec import AgentConfigSpec, LLMSpec, QueueSpec, TriggerSpec

from olib.py.django.test.cases import OTransactionTestCase


def _minimal_spec(*, triggers: list[TriggerSpec], queues: list[QueueSpec] | None = None) -> AgentConfigSpec:
    return AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        triggers=triggers,
        queues=queues or [],
    )


def _queue_agent_with_trigger(
    *,
    username: str,
    identifier: str,
    max_sessions: int = 1,
    queue_id: str = 'inbox',
) -> tuple[Agent, Trigger, Queue]:
    """Create an agent with a queue trigger bound to a queue row."""
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


class TestPutItemDispatch(OTransactionTestCase):
    @patch('celery.current_app.send_task')
    def test_put_item_enqueues_dispatch_task(self, mock_send_task: MagicMock) -> None:
        queue, _session = make_test_queue(identifier='put-dispatch-agent')
        commands.put_item(queue=queue, payload={'hello': 'world'})
        mock_send_task.assert_called_once_with(
            'apps.runner.trigger_tasks.dispatch_queue_triggers_for_queue',
            args=[str(queue.id)],
        )

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_put_item_slots_full_item_stays_available(self, mock_push: MagicMock) -> None:
        agent, trigger, queue = _queue_agent_with_trigger(
            username='put-dispatch-full',
            identifier='put-dispatch-full-agent',
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

        def run_dispatch_immediately(
            name: str,
            args: tuple[str, ...] = (),
            kwargs: dict[str, object] | None = None,
            **opts: object,
        ) -> MagicMock:
            del kwargs, opts
            if name == 'apps.runner.trigger_tasks.dispatch_queue_triggers_for_queue':
                dispatch_queue_triggers_for_queue(queue_pk=args[0])
            return MagicMock()

        with patch('celery.current_app.send_task', side_effect=run_dispatch_immediately):
            put_result = commands.put_item(queue=queue, payload={'work': True})

        item = queue.items.get(pk=put_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.AVAILABLE)
        self.assertEqual(AgentSession.objects.filter(agent=agent, trigger_ref=trigger.id).count(), 1)
        mock_push.assert_not_called()
