# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from apps.agents.models import Agent, AgentConfig, SpendPolicy
from apps.runner.backends.django import DjangoSessionBackend
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
    HourlyUsage,
)
from apps.sessions.tasks import aggregate_hourly_usage
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestHourlyUsageModel(OTestCase):
    def test_activity_has_hourly_aggregation_index(self) -> None:
        """Activity metadata indexes both creation and terminal-transition discovery."""
        index_fields = {tuple(index.fields) for index in AgentSessionActivity._meta.indexes}
        self.assertIn(('kind', 'status', 'created_at'), index_fields)
        self.assertIn(('kind', 'status', 'ended_at'), index_fields)

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
    def _create_activity(
        self,
        agent: Agent,
        *,
        kind: str,
        status: str = AgentSessionActivityStatus.SUCCEEDED,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost: Decimal | None = None,
    ) -> AgentSessionActivity:
        """Create one usage-relevant activity in its own completed session."""
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type='trigger',
        )
        return AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            revision=1,
            kind=kind,
            status=status,
            name=kind,
            summary='',
            details={},
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    def _create_llm(
        self,
        agent: Agent,
        cost: Decimal,
        *,
        model: str = 'gpt-5.4-mini',
        status: str = AgentSessionActivityStatus.SUCCEEDED,
    ) -> AgentSessionActivity:
        """Create one terminal LLM activity with representative usage."""
        return self._create_activity(
            agent,
            kind=AgentSessionActivityKind.LLM,
            status=status,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost=cost,
        )

    def _setup_agent(self, username: str, identifier: str) -> Agent:
        """Create a user + agent with a current config."""
        user = User.objects.create_user(username=username, password='x')
        agent = Agent.objects.create(user=user, name=identifier, identifier=identifier)
        config = AgentConfig.objects.create(
            agent=agent,
            spec={'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'}, 'system_prompt': 'hi', 'schema_version': 4},
            spec_version=4,
        )
        agent.current_config = config
        agent.save()
        return agent

    def test_aggregates_terminal_llm_activities_into_hourly_usage(self) -> None:
        agent = self._setup_agent('agg-test', 'agg-agent')
        self._create_llm(agent, Decimal('0.010000'))
        self._create_llm(agent, Decimal('0.020000'))

        aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.cost_usd, Decimal('0.030000'))
        self.assertEqual(row.iteration_count, 2)
        self.assertEqual(row.input_tokens, 200)

    def test_failed_and_cancelled_llm_activities_count_as_terminal_usage(self) -> None:
        agent = self._setup_agent('agg-terminal', 'terminal-agent')
        self._create_llm(
            agent,
            Decimal('0.010000'),
            status=AgentSessionActivityStatus.FAILED,
        )
        self._create_llm(
            agent,
            Decimal('0.020000'),
            status=AgentSessionActivityStatus.CANCELLED,
        )
        self._create_llm(
            agent,
            Decimal('0.040000'),
            status=AgentSessionActivityStatus.RUNNING,
        )

        aggregate_hourly_usage()

        row = HourlyUsage.objects.get(agent=agent)
        self.assertEqual(row.iteration_count, 2)
        self.assertEqual(row.input_tokens, 200)
        self.assertEqual(row.cost_usd, Decimal('0.030000'))

    def test_output_children_never_contribute_usage_or_cost(self) -> None:
        agent = self._setup_agent('agg-output', 'output-agent')
        llm = self._create_llm(agent, Decimal('0.010000'))
        AgentSessionActivity.objects.create(
            session=llm.session,
            parent=llm,
            seq=2,
            revision=1,
            kind=AgentSessionActivityKind.OUTPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='output',
            summary='',
            details={'content': 'done'},
            model='gpt-5.4-mini',
            input_tokens=900,
            output_tokens=800,
            cost_usd=Decimal('9.000000'),
        )

        aggregate_hourly_usage()

        row = HourlyUsage.objects.get(agent=agent)
        self.assertEqual(row.iteration_count, 1)
        self.assertEqual(row.input_tokens, 100)
        self.assertEqual(row.output_tokens, 50)
        self.assertEqual(row.cost_usd, Decimal('0.010000'))

    def test_aggregation_is_idempotent(self) -> None:
        agent = self._setup_agent('agg-idem', 'idem-agent')
        self._create_llm(agent, Decimal('0.010000'))

        aggregate_hourly_usage()
        aggregate_hourly_usage()  # second run should not double-count

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().cost_usd, Decimal('0.010000'))

    def test_separates_by_model(self) -> None:
        agent = self._setup_agent('agg-model', 'model-agent')
        self._create_llm(agent, Decimal('0.010000'), model='gpt-5.4-mini')
        self._create_llm(agent, Decimal('0.020000'), model='claude-sonnet-4-6')

        aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent).order_by('model')
        self.assertEqual(rows.count(), 2)

    def test_counts_only_terminal_tool_activities_once_across_models(self) -> None:
        agent = self._setup_agent('agg-tools', 'tools-agent')
        self._create_llm(agent, Decimal('0.001000'), model='gpt-5.4-mini')
        self._create_llm(agent, Decimal('0.002000'), model='claude-sonnet-4-6')
        for status in (
            AgentSessionActivityStatus.SUCCEEDED,
            AgentSessionActivityStatus.FAILED,
            AgentSessionActivityStatus.CANCELLED,
            AgentSessionActivityStatus.RUNNING,
            AgentSessionActivityStatus.PENDING,
        ):
            self._create_activity(
                agent,
                kind=AgentSessionActivityKind.TOOL,
                status=status,
            )

        aggregate_hourly_usage()

        rows = list(HourlyUsage.objects.filter(agent=agent))
        self.assertEqual(sum(row.tool_call_count for row in rows), 3)
        self.assertEqual(sum(row.iteration_count for row in rows), 2)

    def test_tool_only_terminal_bucket_is_reported(self) -> None:
        agent = self._setup_agent('agg-tool-only', 'tool-only-agent')
        self._create_activity(
            agent,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.FAILED,
        )

        aggregate_hourly_usage()

        row = HourlyUsage.objects.get(agent=agent)
        self.assertEqual(row.model, '')
        self.assertEqual(row.tool_call_count, 1)
        self.assertEqual(row.iteration_count, 0)

    def test_rerun_keeps_complete_earliest_hour_bucket(self) -> None:
        """Minute-level reruns retain earlier activity in the included cutoff hour."""
        agent = self._setup_agent('agg-hour-cutoff', 'hour-cutoff-agent')
        llm = self._create_llm(agent, Decimal('0.010000'))
        tool = self._create_activity(
            agent,
            kind=AgentSessionActivityKind.TOOL,
        )
        cutoff_hour = datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
        AgentSessionActivity.objects.filter(pk=llm.pk).update(created_at=cutoff_hour.replace(minute=10))
        AgentSessionActivity.objects.filter(pk=tool.pk).update(created_at=cutoff_hour.replace(minute=40))

        with patch(
            'apps.sessions.tasks.timezone.now',
            return_value=cutoff_hour.replace(hour=20),
        ):
            aggregate_hourly_usage()
        with patch(
            'apps.sessions.tasks.timezone.now',
            return_value=cutoff_hour.replace(hour=20, minute=30),
        ):
            aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.hour, cutoff_hour)
        self.assertEqual(row.model, 'gpt-5.4-mini')
        self.assertEqual(row.input_tokens, 100)
        self.assertEqual(row.output_tokens, 50)
        self.assertEqual(row.cost_usd, Decimal('0.010000'))
        self.assertEqual(row.iteration_count, 1)
        self.assertEqual(row.tool_call_count, 1)

    def test_recent_terminal_transition_rebuilds_complete_created_hour(self) -> None:
        """A late completion refreshes its original UTC hour with all terminal work."""
        agent = self._setup_agent('agg-late-terminal', 'late-terminal-agent')
        companion = self._create_llm(agent, Decimal('0.010000'))
        late = self._create_llm(agent, Decimal('0.020000'))
        now = datetime(2026, 7, 26, 20, 30, tzinfo=UTC)
        created_hour = now.replace(hour=16, minute=0)
        AgentSessionActivity.objects.filter(pk=companion.pk).update(
            created_at=created_hour.replace(minute=5),
            ended_at=created_hour.replace(minute=6),
        )
        AgentSessionActivity.objects.filter(pk=late.pk).update(
            created_at=created_hour.replace(minute=10),
            ended_at=now.replace(minute=20),
        )

        with patch('apps.sessions.tasks.timezone.now', return_value=now):
            aggregate_hourly_usage()
            aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.hour, created_hour)
        self.assertEqual(row.input_tokens, 200)
        self.assertEqual(row.output_tokens, 100)
        self.assertEqual(row.cost_usd, Decimal('0.030000'))
        self.assertEqual(row.iteration_count, 2)

    def test_runner_backend_activities_are_aggregated(self) -> None:
        """Canonical backend LLM and tool lifecycles feed hourly aggregation."""
        agent = self._setup_agent('agg-live-runner', 'live-runner-agent')
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type='trigger',
        )
        backend = DjangoSessionBackend(session)
        llm_record = backend.create_activity(
            kind=AgentSessionActivityKind.LLM,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='gpt-5.4-mini',
            summary='generate',
            details={},
            model='gpt-5.4-mini',
            input_tokens=20,
            output_tokens=4,
            cost_usd=Decimal('0.002000'),
            latency_ms=5,
        )
        self.assertEqual(llm_record.model, 'gpt-5.4-mini')
        self.assertEqual(llm_record.input_tokens, 20)
        self.assertEqual(llm_record.output_tokens, 4)
        self.assertEqual(llm_record.cost_usd, Decimal('0.002000'))
        tool_record = backend.create_activity(
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.RUNNING,
            name='clock__now',
            summary='clock__now',
            details={
                'call_id': 'live-tool-1',
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
            },
        )
        backend.update_activity(
            tool_record.id,
            status=AgentSessionActivityStatus.SUCCEEDED,
            summary='clock__now completed',
            details={
                **tool_record.details,
                'result': '2026-07-26T20:00:00+00:00',
            },
            latency_ms=2,
        )

        aggregate_hourly_usage()

        row = HourlyUsage.objects.get(agent=agent)
        self.assertEqual(row.model, 'gpt-5.4-mini')
        self.assertEqual(row.input_tokens, 20)
        self.assertEqual(row.output_tokens, 4)
        self.assertEqual(row.cost_usd, Decimal('0.002000'))
        self.assertEqual(row.iteration_count, 1)
        self.assertEqual(row.tool_call_count, 1)

    def test_bucket_replacement_rolls_back_as_one_unit(self) -> None:
        """A write failure cannot expose a partially replaced hourly snapshot."""
        agent = self._setup_agent('agg-rollback', 'rollback-agent')
        first = self._create_llm(agent, Decimal('0.010000'), model='gpt-5.4-mini')
        self._create_llm(agent, Decimal('0.020000'), model='claude-sonnet-4-6')
        hour = first.created_at.replace(minute=0, second=0, microsecond=0)
        for model in ('gpt-5.4-mini', 'claude-sonnet-4-6'):
            HourlyUsage.objects.create(
                agent=agent,
                hour=hour,
                model=model,
                input_tokens=7,
                output_tokens=8,
                cost_usd=Decimal('7.000000'),
                iteration_count=9,
            )

        original_update_or_create = HourlyUsage.objects.update_or_create
        call_count = 0

        def fail_second_write(*args: Any, **kwargs: Any) -> Any:
            """Let one replacement write through before simulating storage failure."""
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError('simulated storage failure')
            return original_update_or_create(*args, **kwargs)

        with patch.object(HourlyUsage.objects, 'update_or_create', side_effect=fail_second_write):
            with self.assertRaises(RuntimeError):
                aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual(row.input_tokens, 7)
            self.assertEqual(row.output_tokens, 8)
            self.assertEqual(row.cost_usd, Decimal('7.000000'))
            self.assertEqual(row.iteration_count, 9)

    def test_aggregation_locks_stable_agent_rows(self) -> None:
        """Rollups serialize overlapping snapshots through stable agent rows."""
        agent = self._setup_agent('agg-lock', 'lock-agent')
        self._create_llm(agent, Decimal('0.010000'))

        with patch(
            'apps.sessions.tasks.Agent.objects.select_for_update',
            wraps=Agent.objects.select_for_update,
        ) as select_for_update:
            aggregate_hourly_usage()

        select_for_update.assert_called_once_with()

    def test_hour_buckets_remain_distinct_across_local_dst_fold(self) -> None:
        """UTC truncation keeps repeated local-clock hours in separate buckets."""
        agent = self._setup_agent('agg-dst', 'dst-agent')
        first = self._create_llm(agent, Decimal('0.010000'))
        second = self._create_llm(agent, Decimal('0.020000'))
        first_hour = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
        second_hour = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
        AgentSessionActivity.objects.filter(pk=first.pk).update(created_at=first_hour.replace(minute=30))
        AgentSessionActivity.objects.filter(pk=second.pk).update(created_at=second_hour.replace(minute=30))

        with timezone.override(ZoneInfo('America/New_York')):
            with patch(
                'apps.sessions.tasks.timezone.now',
                return_value=datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
            ):
                aggregate_hourly_usage()

        self.assertEqual(
            list(HourlyUsage.objects.filter(agent=agent).order_by('hour').values_list('hour', flat=True)),
            [first_hour, second_hour],
        )
