# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging
from unittest.mock import MagicMock, patch

from apps.agents.services.config_commands import create_from_example
from apps.sessions.models import AgentSession
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestAgentStartChatView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='start-chat-user', password='test')
        self.other = User.objects.create_user(username='other-start-chat-user', password='test')
        self.agent = create_from_example(
            self.user,
            'clock-assistant',
            identifier='start-chat-agent',
        )

    def test_requires_login(self) -> None:
        response = self.client.post(
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
            {'content': 'hello'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/loelabs-admin/login/', response['Location'])

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Bad Request: /agents/[0-9a-f-]+/chat/', count=1)]
    )
    def test_requires_content(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 400)

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_creates_session_with_initial_message(self, mock_push: MagicMock) -> None:
        self.client.force_login(self.user)
        before = AgentSession.objects.filter(agent=self.agent).count()
        response = self.client.post(
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
            {'content': 'hello'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AgentSession.objects.filter(agent=self.agent).count(), before + 1)
        session = AgentSession.objects.filter(agent=self.agent).order_by('-created_at').first()
        assert session is not None
        self.assertEqual(
            response['Location'],
            reverse('session_detail', kwargs={'session_id': session.id}),
        )
        mock_push.assert_called_once_with(session.id, 'hello')

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/chat/', count=1)])
    def test_cannot_start_chat_on_other_users_agent(self) -> None:
        self.client.force_login(self.other)
        response = self.client.post(
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
            {'content': 'hello'},
        )
        self.assertEqual(response.status_code, 404)

    def test_agent_page_chat_form_posts_to_start_chat(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertContains(
            response,
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
        )

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_agent_start_chat_lands_on_session_with_same_frame(self, mock_push: MagicMock) -> None:
        self.client.force_login(self.user)
        agent_page = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertContains(agent_page, 'frame-chatbox')

        response = self.client.post(
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
            {'content': 'hello there'},
        )
        session_page = self.client.get(response['Location'])
        self.assertEqual(session_page.status_code, 200)
        self.assertContains(session_page, 'frame-chatbox')
        self.assertContains(session_page, 'event-panel')
        self.assertContains(session_page, 'Events')
        mock_push.assert_called_once()
