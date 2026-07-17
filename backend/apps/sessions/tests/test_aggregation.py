# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from decimal import Decimal

from apps.agents.models import Agent, AgentConfig, SpendPolicy
from apps.sessions.models import (
    AgentSession,
    AgentSessionEvent,
    AgentSessionEventKind,
    AgentSessionStatus,
    HourlyUsage,
)
from apps.sessions.tasks import aggregate_hourly_usage
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestHourlyUsageModel(OTestCase):
    def test_create_hourly_usage_row(self) -> None:
        user = User.objects.create_user(username='limittest', password='x')
        agent = Agent.objects.create(user=user, name='Test', identifier='test-agent')
        row = HourlyUsage.objects.create(
            agent=agent,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal('0.001'),
            iteration_count=1,
            tool_call_count=2,
        )
        self.assertEqual(row.iteration_count, 1)

    def test_agent_spend_limit_fields(self) -> None:
        user = User.objects.create_user(username='limittest2', password='x')
        agent = Agent.objects.create(
            user=user,
            name='Test',
            identifier='test-agent-2',
            daily_spend_limit_usd=Decimal('10.00'),
            monthly_spend_limit_usd=Decimal('100.00'),
        )
        agent.refresh_from_db()
        self.assertEqual(agent.daily_spend_limit_usd, Decimal('10.00'))

    def test_spend_policy_model(self) -> None:
        user = User.objects.create_user(username='limittest3', password='x')
        policy = SpendPolicy.objects.create(
            user=user,
            daily_spend_limit_usd=Decimal('50.00'),
            monthly_spend_limit_usd=Decimal('500.00'),
        )
        self.assertEqual(policy.daily_spend_limit_usd, Decimal('50.00'))


class TestAggregateHourlyUsage(OTestCase):
    def _create_output_event(self, agent: Agent, cost: Decimal, model: str = 'gpt-5.4-mini') -> None:
        """Create a session with one OUTPUT event carrying the given cost."""
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type='trigger',
        )
        AgentSessionEvent.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionEventKind.OUTPUT,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost_usd=cost,
        )

    def _setup_agent(self, username: str, identifier: str) -> Agent:
        """Create a user + agent with a current config."""
        user = User.objects.create_user(username=username, password='x')
        agent = Agent.objects.create(user=user, name=identifier, identifier=identifier)
        config = AgentConfig.objects.create(
            agent=agent,
            spec={'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'}, 'system_prompt': 'hi', 'schema_version': 3},
            spec_version=3,
        )
        agent.current_config = config
        agent.save()
        return agent

    def test_aggregates_output_events_into_hourly_usage(self) -> None:
        agent = self._setup_agent('agg-test', 'agg-agent')
        self._create_output_event(agent, Decimal('0.010000'))
        self._create_output_event(agent, Decimal('0.020000'))

        aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.cost_usd, Decimal('0.030000'))
        self.assertEqual(row.iteration_count, 2)
        self.assertEqual(row.input_tokens, 200)

    def test_aggregation_is_idempotent(self) -> None:
        agent = self._setup_agent('agg-idem', 'idem-agent')
        self._create_output_event(agent, Decimal('0.010000'))

        aggregate_hourly_usage()
        aggregate_hourly_usage()  # second run should not double-count

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().cost_usd, Decimal('0.010000'))

    def test_separates_by_model(self) -> None:
        agent = self._setup_agent('agg-model', 'model-agent')
        self._create_output_event(agent, Decimal('0.010000'), model='gpt-5.4-mini')
        self._create_output_event(agent, Decimal('0.020000'), model='claude-sonnet-4-6')

        aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent).order_by('model')
        self.assertEqual(rows.count(), 2)

    def test_counts_tool_calls(self) -> None:
        agent = self._setup_agent('agg-tools', 'tools-agent')
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type='trigger',
        )
        AgentSessionEvent.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionEventKind.OUTPUT,
            model='gpt-5.4-mini',
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal('0.001'),
        )
        AgentSessionEvent.objects.create(
            session=session,
            seq=2,
            kind=AgentSessionEventKind.TOOL_CALL,
            payload={'call_id': 'tc1', 'instance_id': 'clock', 'function': 'now', 'arguments': {}},
        )
        AgentSessionEvent.objects.create(
            session=session,
            seq=3,
            kind=AgentSessionEventKind.TOOL_CALL,
            payload={'call_id': 'tc2', 'instance_id': 'clock', 'function': 'now', 'arguments': {}},
        )

        aggregate_hourly_usage()

        row = HourlyUsage.objects.get(agent=agent)
        self.assertEqual(row.tool_call_count, 2)
        self.assertEqual(row.iteration_count, 1)
