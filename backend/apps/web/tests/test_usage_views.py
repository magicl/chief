# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests verifying dashboard and agent detail views pass usage context to templates."""

from __future__ import annotations

from decimal import Decimal

from apps.agents.models import SpendPolicy
from apps.agents.services.config_commands import create_from_example
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTestCase


class TestDashboardUsageContext(OTestCase):
    """Dashboard view renders user-level spend figures for authenticated users."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = get_user_model().objects.create_user(username='usage-dash', password='testpass')
        self.client.login(username='usage-dash', password='testpass')

    def test_dashboard_includes_spend_display(self) -> None:
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b'Daily spend')
        self.assertContains(response, b'Monthly spend')

    def test_dashboard_shows_limit_with_spend_policy(self) -> None:
        SpendPolicy.objects.create(
            user=self.user,
            daily_spend_limit_usd=Decimal('5.00'),
            monthly_spend_limit_usd=Decimal('50.00'),
        )
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '$5.00')
        self.assertContains(response, '$50.00')


class TestAgentDetailUsageContext(OTestCase):
    """Agent detail view renders agent-level spend figures."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = get_user_model().objects.create_user(username='usage-agent', password='testpass')
        self.client.login(username='usage-agent', password='testpass')
        self.agent = create_from_example(self.user, 'clock-assistant', identifier='usage-agent')

    def test_agent_detail_includes_spend_display(self) -> None:
        url = reverse('agent_detail', kwargs={'agent_id': self.agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, b'Daily spend')
        self.assertContains(response, b'Monthly spend')

    def test_agent_detail_shows_limit_when_set(self) -> None:
        self.agent.daily_spend_limit_usd = Decimal('2.00')
        self.agent.monthly_spend_limit_usd = Decimal('20.00')
        self.agent.save(update_fields=['daily_spend_limit_usd', 'monthly_spend_limit_usd'])

        url = reverse('agent_detail', kwargs={'agent_id': self.agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '$2.00')
        self.assertContains(response, '$20.00')
