# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Integration tests for scheduling budget gate enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, Trigger
from apps.queues.models import Queue
from apps.queues.services import commands
from apps.runner.scheduling import dispatch_queue_triggers, dispatch_schedule_trigger
from apps.sessions.models import AgentSession, HourlyUsage
from django.contrib.auth import get_user_model
from django.utils import timezone
from libs.agent_spec import AgentConfigSpec, LLMSpec, QueueSpec, TriggerSpec

from olib.py.django.test.cases import OTestCase


def _spec(
    *,
    triggers: list[TriggerSpec],
    queues: list[QueueSpec] | None = None,
) -> AgentConfigSpec:
    """Minimal agent config with given triggers."""
    return AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        triggers=triggers,
        queues=queues or [],
    )


class TestScheduleGateIntegration(OTestCase):
    """Verify the budget gate blocks or allows schedule dispatch end-to-end."""

    def _schedule_agent(
        self,
        *,
        username: str,
        daily_limit: Decimal | None = None,
        monthly_limit: Decimal | None = None,
    ) -> tuple[Agent, Trigger]:
        """Create an agent with a schedule trigger and optional spend limits."""
        user = get_user_model().objects.create_user(username=username, password='x')
        agent = Agent.objects.create(
            user=user,
            name='Gate',
            identifier=username,
            daily_spend_limit_usd=daily_limit,
            monthly_spend_limit_usd=monthly_limit,
        )
        spec = _spec(
            triggers=[
                TriggerSpec(name='manual', kind='manual'),
                TriggerSpec(name='sweep', kind='schedule', cron='0 * * * *', prompt='Run.'),
            ],
        )
        config = persist_agent_config(agent, spec, source_rev=f'{username}-v1')
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        return agent, trigger

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_dispatch_blocked_when_daily_budget_exceeded(self, mock_push: MagicMock) -> None:
        agent, trigger = self._schedule_agent(
            username='gate-daily-over',
            daily_limit=Decimal('1.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('1.50'),
        )

        result = dispatch_schedule_trigger(
            trigger_id=trigger.id,
            now=datetime(2026, 7, 5, 14, 0, tzinfo=UTC),
        )

        self.assertFalse(result)
        self.assertFalse(AgentSession.objects.filter(agent=agent).exists())
        mock_push.assert_not_called()

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_dispatch_allowed_when_under_daily_budget(self, mock_push: MagicMock) -> None:
        agent, trigger = self._schedule_agent(
            username='gate-daily-under',
            daily_limit=Decimal('10.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('2.00'),
        )

        result = dispatch_schedule_trigger(
            trigger_id=trigger.id,
            now=datetime(2026, 7, 5, 14, 0, tzinfo=UTC),
        )

        self.assertTrue(result)
        self.assertTrue(AgentSession.objects.filter(agent=agent).exists())
        mock_push.assert_called_once()

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_dispatch_blocked_when_monthly_budget_exceeded(self, mock_push: MagicMock) -> None:
        agent, trigger = self._schedule_agent(
            username='gate-monthly-over',
            monthly_limit=Decimal('5.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('5.00'),
        )

        result = dispatch_schedule_trigger(
            trigger_id=trigger.id,
            now=datetime(2026, 7, 5, 14, 0, tzinfo=UTC),
        )

        self.assertFalse(result)
        self.assertFalse(AgentSession.objects.filter(agent=agent).exists())
        mock_push.assert_not_called()


class TestQueueGateIntegration(OTestCase):
    """Verify the budget gate blocks queue dispatch when spend is exceeded."""

    def _queue_agent(
        self,
        *,
        username: str,
        monthly_limit: Decimal | None = None,
    ) -> tuple[Agent, Trigger, Queue]:
        """Create an agent with a queue trigger and optional monthly spend limit."""
        user = get_user_model().objects.create_user(username=username, password='x')
        agent = Agent.objects.create(
            user=user,
            name='QueueGate',
            identifier=username,
            monthly_spend_limit_usd=monthly_limit,
        )
        spec = _spec(
            triggers=[
                TriggerSpec(name='manual', kind='manual'),
                TriggerSpec(name='worker', kind='queue', queue='inbox', prompt='Process.'),
            ],
            queues=[QueueSpec(id='inbox')],
        )
        config = persist_agent_config(agent, spec, source_rev=f'{username}-v1')
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='worker')
        queue = Queue.objects.get(agent=agent, queue_id='inbox')
        return agent, trigger, queue

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_queue_dispatch_blocked_when_monthly_budget_exceeded(self, mock_push: MagicMock) -> None:
        agent, _trigger, queue = self._queue_agent(
            username='qgate-monthly-over',
            monthly_limit=Decimal('5.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('6.00'),
        )
        commands.put_item(queue=queue, payload={'subject': 'hello'})

        stats = dispatch_queue_triggers()

        self.assertEqual(stats.queue_sessions, 0)
        self.assertFalse(AgentSession.objects.filter(agent=agent).exists())
        mock_push.assert_not_called()
