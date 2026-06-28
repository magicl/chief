# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging

from apps.agents.models import Agent
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_BOOTSTRAP_POST = {'provider': 'openai', 'model': 'gpt-5.4-mini'}


class TestBootstrapAgentView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='bootstrap-user', password='test')
        self.other = User.objects.create_user(username='other-user', password='test')

    def test_requires_login(self) -> None:
        response = self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Bad Request: /agents/bootstrap/', count=1)])
    def test_requires_provider_and_model(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(reverse('bootstrap_agent'))
        self.assertEqual(response.status_code, 400)

    def test_creates_agent_for_current_user(self) -> None:
        self.client.force_login(self.user)
        before = Agent.objects.filter(user=self.user).count()
        response = self.client.post(
            reverse('bootstrap_agent'),
            {'provider': 'anthropic', 'model': 'claude-haiku-4-5'},
        )
        self.assertEqual(response.status_code, 302)
        agent = Agent.objects.filter(user=self.user).order_by('-id').first()
        assert agent is not None
        self.assertEqual(
            response['Location'],
            reverse('agent_detail', kwargs={'agent_id': agent.id}),
        )
        after = Agent.objects.filter(user=self.user).count()
        self.assertEqual(after, before + 1)
        agent = Agent.objects.filter(user=self.user).order_by('-id').first()
        assert agent is not None
        self.assertIsNotNone(agent.current_config)
        assert agent.current_config is not None
        self.assertEqual(agent.current_config.spec['llm']['provider'], 'anthropic')
        self.assertEqual(agent.current_config.spec['llm']['model'], 'claude-haiku-4-5')
        self.assertEqual(agent.triggers.count(), 1)

    def test_bootstrap_lands_on_agent_page(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('bootstrap_agent'),
            {'provider': 'openai', 'model': 'gpt-5.4-mini'},
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response['Location'])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'frame-chatbox')
        self.assertContains(page, 'Message the agent')

    def test_dashboard_lists_demo_model_buttons(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OpenAI · gpt-5.4-mini')
        self.assertContains(response, 'Anthropic · claude-haiku-4-5')
        self.assertContains(response, 'Local · llama3.2')

    def test_each_click_creates_new_agent(self) -> None:
        self.client.force_login(self.user)
        self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        agents = Agent.objects.filter(user=self.user)
        self.assertEqual(agents.count(), 2)
        self.assertEqual(len({a.identifier for a in agents}), 2)

    def test_dashboard_shows_only_own_agents(self) -> None:
        from apps.agents.hardcoded import bootstrap_agent

        bootstrap_agent(
            self.other,
            identifier='other-agent',
            provider='openai',
            model='gpt-5.4-mini',
        )
        self.client.force_login(self.user)
        self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'other-agent')
        self.assertContains(response, self.user.username)

    def test_delete_agent_removes_own_agent(self) -> None:
        self.client.force_login(self.user)
        self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        agent = Agent.objects.filter(user=self.user).first()
        assert agent is not None
        response = self.client.post(reverse('delete_agent', kwargs={'agent_id': agent.id}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Agent.objects.filter(pk=agent.id).exists())

    def test_delete_agent_with_sessions(self) -> None:
        from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType

        self.client.force_login(self.user)
        self.client.post(reverse('bootstrap_agent'), _BOOTSTRAP_POST)
        agent = Agent.objects.filter(user=self.user).first()
        assert agent is not None
        config = agent.current_config
        assert config is not None
        trigger = agent.triggers.filter(name='manual').first()
        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.QUEUED,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )
        self.assertTrue(AgentSession.objects.filter(agent=agent).exists())
        response = self.client.post(reverse('delete_agent', kwargs={'agent_id': agent.id}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Agent.objects.filter(pk=agent.id).exists())
        self.assertFalse(AgentSession.objects.filter(agent_id=agent.id).exists())

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/delete/', count=1)])
    def test_delete_agent_rejects_other_users_agent(self) -> None:
        from apps.agents.hardcoded import bootstrap_agent

        other_agent = bootstrap_agent(
            self.other,
            identifier='protected-agent',
            provider='openai',
            model='gpt-5.4-mini',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('delete_agent', kwargs={'agent_id': other_agent.id}))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Agent.objects.filter(pk=other_agent.id).exists())
