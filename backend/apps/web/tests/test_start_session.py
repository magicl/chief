# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging

from apps.agents.models import AgentStatus
from apps.agents.services.config_commands import create_from_example
from apps.sessions.models import AgentSession
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestStartAgentSessionView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='start-user', password='test')
        self.other = User.objects.create_user(username='other-user', password='test')
        self.agent = create_from_example(
            self.user,
            'clock-assistant',
            identifier='start-agent',
        )

    def test_requires_login(self) -> None:
        response = self.client.post(reverse('start_agent_session', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/loelabs-admin/login/', response['Location'])

    def test_creates_session_and_redirects(self) -> None:
        self.client.force_login(self.user)
        before = AgentSession.objects.filter(agent=self.agent).count()
        response = self.client.post(reverse('start_agent_session', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 302)
        session = AgentSession.objects.filter(agent=self.agent).order_by('-created_at').first()
        assert session is not None
        self.assertEqual(AgentSession.objects.filter(agent=self.agent).count(), before + 1)
        self.assertEqual(response['Location'], reverse('session_detail', kwargs={'session_id': session.id}))
        page = self.client.get(response['Location'])
        self.assertEqual(page.status_code, 200)

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Bad Request: /agents/[0-9a-f-]+/start/', count=1)]
    )
    def test_disabled_agent_returns_clear_failure(self) -> None:
        self.client.force_login(self.user)
        self.agent.status = AgentStatus.DISABLED
        self.agent.save(update_fields=['status'])

        response = self.client.post(reverse('start_agent_session', kwargs={'agent_id': self.agent.id}))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'is disabled', response.content)
        self.assertFalse(AgentSession.objects.filter(agent=self.agent).exists())

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/start/', count=1)]
    )
    def test_cannot_start_other_users_agent(self) -> None:
        self.client.force_login(self.other)
        response = self.client.post(reverse('start_agent_session', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 404)
