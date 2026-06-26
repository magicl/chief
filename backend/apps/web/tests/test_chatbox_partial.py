# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.hardcoded import bootstrap_agent
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


class TestChatboxPartial(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='chatbox-user', password='test')
        self.agent = bootstrap_agent(
            self.user,
            identifier='chatbox-agent',
            provider='openai',
            model='gpt-5.4-mini',
        )
        config = self.agent.current_config
        assert config is not None
        trigger = self.agent.triggers.filter(name='manual').first()
        self.session = AgentSession.objects.create(
            agent=self.agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )

    def test_agent_detail_includes_shared_chatbox(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertContains(response, 'class="frame-chatbox"')
        self.assertContains(
            response,
            reverse('agent_start_chat', kwargs={'agent_id': self.agent.id}),
        )
        self.assertContains(response, 'Enter to send')

    def test_session_detail_includes_shared_chatbox(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'class="frame-chatbox"')
        self.assertContains(
            response,
            reverse('session_chat', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'Enter to send')

    def test_session_detail_renders_shared_chatbox_once(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        body = response.content.decode()
        self.assertEqual(body.count('name="content"'), 1)
        self.assertEqual(body.count('class="frame-chatbox"'), 1)

    def test_session_detail_does_not_include_old_sidebar_chat(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertNotContains(response, 'controls-panel')
        self.assertNotContains(response, 'id="chat-messages"')
