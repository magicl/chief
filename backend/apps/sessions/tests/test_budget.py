# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from datetime import timedelta
from decimal import Decimal

from apps.agents.models import Agent
from apps.sessions.models import HourlyUsage
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    compute_effective_spend_cap,
    user_daily_spend,
    user_monthly_spend,
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
            agent=self.agent,
            hour=earlier_this_month,
            model='m',
            cost_usd=Decimal('10.000000'),
            iteration_count=50,
        )
        HourlyUsage.objects.create(
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
            agent=self.agent,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
            iteration_count=5,
        )
        HourlyUsage.objects.create(
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
            agent=self.agent,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('1.000000'),
            iteration_count=5,
        )
        HourlyUsage.objects.create(
            agent=agent2,
            hour=self.today_hour,
            model='m',
            cost_usd=Decimal('2.000000'),
            iteration_count=5,
        )
        result = user_monthly_spend(self.user.id)
        self.assertEqual(result, Decimal('3.000000'))


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
