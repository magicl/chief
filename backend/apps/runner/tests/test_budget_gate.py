# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for pre-dispatch budget gate."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from apps.agents.models import Agent, SpendPolicy
from apps.runner.budget_gate import budget_allows_dispatch
from apps.sessions.models import HourlyUsage
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase


class TestBudgetAllowsDispatch(OTestCase):
    def _create_agent(
        self,
        *,
        daily_limit: Decimal | None = None,
        monthly_limit: Decimal | None = None,
    ) -> Agent:
        """Create a user + agent with optional spend limits."""
        user = get_user_model().objects.create_user(username=f'budget-gate-{uuid4().hex[:8]}', password='x')
        return Agent.objects.create(
            user=user,
            name='Gate Test',
            identifier=f'gate-{uuid4().hex[:8]}',
            daily_spend_limit_usd=daily_limit,
            monthly_spend_limit_usd=monthly_limit,
        )

    def test_allows_when_no_limits(self) -> None:
        agent = self._create_agent()
        self.assertTrue(budget_allows_dispatch(agent))

    def test_blocks_when_agent_daily_exceeded(self) -> None:
        agent = self._create_agent(daily_limit=Decimal('1.00'))
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('1.50'),
        )
        self.assertFalse(budget_allows_dispatch(agent))

    def test_blocks_when_agent_monthly_exceeded(self) -> None:
        agent = self._create_agent(monthly_limit=Decimal('10.00'))
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('15.00'),
        )
        self.assertFalse(budget_allows_dispatch(agent))

    def test_blocks_when_user_daily_exceeded(self) -> None:
        agent = self._create_agent()
        SpendPolicy.objects.create(
            user_id=agent.user_id,
            daily_spend_limit_usd=Decimal('2.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('3.00'),
        )
        self.assertFalse(budget_allows_dispatch(agent))

    def test_blocks_when_user_monthly_exceeded(self) -> None:
        agent = self._create_agent()
        SpendPolicy.objects.create(
            user_id=agent.user_id,
            monthly_spend_limit_usd=Decimal('20.00'),
        )
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('25.00'),
        )
        self.assertFalse(budget_allows_dispatch(agent))

    def test_allows_when_under_limits(self) -> None:
        agent = self._create_agent(daily_limit=Decimal('5.00'), monthly_limit=Decimal('50.00'))
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('1.00'),
        )
        self.assertTrue(budget_allows_dispatch(agent))

    def test_agent_daily_at_exact_limit_blocks(self) -> None:
        """Spend exactly at the cap should be treated as exceeded (>= check)."""
        agent = self._create_agent(daily_limit=Decimal('5.00'))
        now = timezone.now()
        HourlyUsage.objects.create(
            agent=agent,
            hour=now.replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('5.00'),
        )
        self.assertFalse(budget_allows_dispatch(agent))
