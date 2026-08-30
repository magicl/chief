# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from datetime import timedelta
from decimal import Decimal

from apps.agents.models import Agent, SpendPolicy
from apps.sessions.models import HourlyUsage
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    algorithm_daily_spend,
    algorithm_monthly_spend,
    compute_effective_spend_cap,
    resolve_user_spend_caps,
    user_daily_spend,
    user_monthly_spend,
    user_rolling_cap_reached,
)
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestBudgetQueries(OTestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username='budget-user', password='x')
        self.agent = Agent.objects.create(user=self.user, name='B', identifier='budget-agent')
        self.now = timezone.now()
        self.today_hour = self.now.replace(minute=0, second=0, microsecond=0)

    def test_agent_daily_spend_sums_today(self) -> None:
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.500000'),
            iteration_count=10,
        )
        result = agent_daily_spend(self.agent.id)
        self.assertEqual(result, Decimal('1.500000'))

    def test_agent_daily_spend_excludes_yesterday(self) -> None:
        yesterday_hour = self.today_hour - timedelta(days=1)
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=yesterday_hour,
            model='m',
            cost_usd=Decimal('5.000000'),
            iteration_count=20,
        )
        result = agent_daily_spend(self.agent.id)
        self.assertEqual(result, Decimal(0))

    def test_agent_monthly_spend_includes_earlier_this_month(self) -> None:
        earlier_this_month = self.today_hour.replace(day=1)
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=earlier_this_month,
            model='m',
            cost_usd=Decimal('10.000000'),
            iteration_count=50,
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=self.today_hour,
            model='m2',
            cost_usd=Decimal('2.000000'),
            iteration_count=10,
        )
        result = agent_monthly_spend(self.agent.id)
        self.assertEqual(result, Decimal('12.000000'))

    def test_user_daily_spend_sums_across_agents(self) -> None:
        agent2 = Agent.objects.create(user=self.user, name='B2', identifier='budget-agent-2')
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
            iteration_count=5,
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=agent2,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('2.000000'),
            iteration_count=5,
        )
        result = user_daily_spend(self.user.id)
        self.assertEqual(result, Decimal('3.000000'))

    def test_user_monthly_spend_sums_across_agents(self) -> None:
        agent2 = Agent.objects.create(user=self.user, name='B2', identifier='budget-agent-2')
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
            iteration_count=5,
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=agent2,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('2.000000'),
            iteration_count=5,
        )
        result = user_monthly_spend(self.user.id)
        self.assertEqual(result, Decimal('3.000000'))

    def test_user_daily_spend_includes_algorithm_bucket(self) -> None:
        HourlyUsage.objects.create(
            user=self.user,
            agent=None,
            algorithm_id='chat_name',
            hour=self.today_hour,
            model='gpt-5.4-nano',
            cost_usd=Decimal('0.400000'),
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            algorithm_id=None,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
            iteration_count=1,
        )
        self.assertEqual(user_daily_spend(self.user.id), Decimal('1.400000'))
        self.assertEqual(agent_daily_spend(self.agent.id), Decimal('1.000000'))

    def test_algorithm_daily_spend_is_scoped_to_user_and_id(self) -> None:
        other = User.objects.create_user(username='budget-other', password='x')
        HourlyUsage.objects.create(
            user=self.user,
            agent=None,
            algorithm_id='chat_name',
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('0.250000'),
        )
        HourlyUsage.objects.create(
            user=other,
            agent=None,
            algorithm_id='chat_name',
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('9.000000'),
        )
        self.assertEqual(algorithm_daily_spend(self.user.id, 'chat_name'), Decimal('0.250000'))

    def test_algorithm_monthly_spend_excludes_agent_and_last_month(self) -> None:
        month_start_hour = self.today_hour.replace(day=1)
        HourlyUsage.objects.create(
            user=self.user,
            agent=None,
            algorithm_id='chat_name',
            hour=month_start_hour,
            model='m',
            cost_usd=Decimal('0.500000'),
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=None,
            algorithm_id='chat_name',
            hour=month_start_hour - timedelta(days=1),
            model='m',
            cost_usd=Decimal('7.000000'),
        )
        HourlyUsage.objects.create(
            user=self.user,
            agent=self.agent,
            algorithm_id=None,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('3.000000'),
            iteration_count=1,
        )
        self.assertEqual(algorithm_monthly_spend(self.user.id, 'chat_name'), Decimal('0.500000'))

    def test_resolve_user_spend_caps_reads_spend_policy(self) -> None:
        self.assertEqual(resolve_user_spend_caps(self.user.id), (None, None))
        SpendPolicy.objects.create(
            user=self.user,
            daily_spend_limit_usd=Decimal('1.00'),
            monthly_spend_limit_usd=Decimal('20.00'),
        )
        self.assertEqual(resolve_user_spend_caps(self.user.id), (Decimal('1.00'), Decimal('20.00')))

    def test_user_rolling_cap_reached_when_daily_met(self) -> None:
        SpendPolicy.objects.create(user=self.user, daily_spend_limit_usd=Decimal('1.00'))
        self.assertFalse(user_rolling_cap_reached(self.user.id))
        HourlyUsage.objects.create(
            user=self.user,
            agent=None,
            algorithm_id='chat_name',
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
        )
        self.assertTrue(user_rolling_cap_reached(self.user.id))


class TestEffectiveSpendCap(OTestCase):
    def test_min_of_all_levels(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=Decimal('5.00'),
            agent_daily_remaining=Decimal('3.00'),
            agent_monthly_remaining=Decimal('50.00'),
            user_daily_remaining=Decimal('10.00'),
            user_monthly_remaining=Decimal('100.00'),
        )
        self.assertEqual(result, Decimal('3.00'))

    def test_none_values_ignored(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=Decimal('5.00'),
            agent_daily_remaining=None,
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )
        self.assertEqual(result, Decimal('5.00'))

    def test_all_none_returns_none(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=None,
            agent_daily_remaining=None,
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )
        self.assertIsNone(result)

    def test_negative_remaining_returns_zero(self) -> None:
        """If already over budget, effective cap should be 0 (or the negative value)."""
        result = compute_effective_spend_cap(
            session_spend_cap=Decimal('5.00'),
            agent_daily_remaining=Decimal('-1.00'),
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )
        self.assertEqual(result, Decimal('-1.00'))
