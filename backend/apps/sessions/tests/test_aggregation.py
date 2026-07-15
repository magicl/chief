# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from decimal import Decimal

from apps.agents.models import Agent, SpendPolicy
from apps.sessions.models import HourlyUsage
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
