# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging

from apps.agents.services.config_commands import create_from_example
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestAgentDetailView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='agent-detail-user', password='test')
        self.other = User.objects.create_user(username='other-detail-user', password='test')
        self.agent = create_from_example(
            self.user,
            'clock-assistant',
            identifier='detail-agent',
        )

    def test_requires_login(self) -> None:
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/loelabs-admin/login/', response['Location'])

    def test_renders_owned_agent(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'detail-agent')
        self.assertContains(response, 'Sessions')
        self.assertContains(response, 'frame-chatbox')
        self.assertContains(response, 'Message the agent')

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/$', count=1)])
    def test_cannot_view_other_users_agent(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 404)

    def test_lists_agent_sessions(self) -> None:
        config = self.agent.current_config
        assert config is not None
        trigger = self.agent.triggers.filter(name='manual').first()
        AgentSession.objects.create(
            agent=self.agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )
        AgentSession.objects.create(
            agent=self.agent,
            agent_config=config,
            status=AgentSessionStatus.QUEUED,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )
        other_agent = create_from_example(
            self.other,
            'clock-assistant',
            identifier='other-detail-agent',
        )
        other_config = other_agent.current_config
        assert other_config is not None
        other_trigger = other_agent.triggers.filter(name='manual').first()
        AgentSession.objects.create(
            agent=other_agent,
            agent_config=other_config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=other_trigger.id if other_trigger else None,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count('class="pill'), 2)

    def test_dashboard_agent_identifier_links_to_detail(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('agent_detail', kwargs={'agent_id': self.agent.id}),
        )

    def test_agent_detail_session_links(self) -> None:
        config = self.agent.current_config
        assert config is not None
        trigger = self.agent.triggers.filter(name='manual').first()
        session = AgentSession.objects.create(
            agent=self.agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertContains(
            response,
            reverse('session_detail', kwargs={'session_id': session.id}),
        )

    def test_frame_layout_classes(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertContains(response, 'frame-page')
        self.assertContains(response, 'frame-main')
        self.assertContains(response, 'chief-frame')
