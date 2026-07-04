# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging

from apps.agents.models import Agent
from apps.agents.services.config_commands import create_from_example
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_CREATE_POST = {'example_slug': 'clock-assistant'}


class TestCreateAgentView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='create-user', password='test')
        self.other = User.objects.create_user(username='other-user', password='test')

    def test_requires_login(self) -> None:
        response = self.client.post(reverse('agent_create'), _CREATE_POST)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Bad Request: /agents/create/', count=1)])
    def test_requires_example_or_yaml(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(reverse('agent_create'))
        self.assertEqual(response.status_code, 400)

    def test_creates_agent_from_example(self) -> None:
        self.client.force_login(self.user)
        before = Agent.objects.filter(user=self.user).count()
        response = self.client.post(reverse('agent_create'), _CREATE_POST)
        self.assertEqual(response.status_code, 302)
        agent = Agent.objects.filter(user=self.user).order_by('-id').first()
        assert agent is not None
        self.assertEqual(
            response['Location'],
            reverse('agent_config', kwargs={'agent_id': agent.id}),
        )
        after = Agent.objects.filter(user=self.user).count()
        self.assertEqual(after, before + 1)
        assert agent.current_config is not None
        self.assertEqual(agent.current_config.spec['llm']['provider'], 'openai')
        self.assertEqual(agent.triggers.count(), 1)

    def test_dashboard_lists_example_buttons(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clock assistant')
        self.assertContains(response, 'Create agent')

    def test_each_click_creates_new_agent(self) -> None:
        self.client.force_login(self.user)
        self.client.post(reverse('agent_create'), _CREATE_POST)
        self.client.post(reverse('agent_create'), _CREATE_POST)
        agents = Agent.objects.filter(user=self.user)
        self.assertEqual(agents.count(), 2)

    def test_dashboard_shows_only_own_agents(self) -> None:
        create_from_example(self.other, 'clock-assistant', identifier='other-agent')
        self.client.force_login(self.user)
        self.client.post(reverse('agent_create'), _CREATE_POST)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'other-agent')
        self.assertContains(response, self.user.username)

    def test_delete_agent_removes_own_agent(self) -> None:
        self.client.force_login(self.user)
        self.client.post(reverse('agent_create'), _CREATE_POST)
        agent = Agent.objects.filter(user=self.user).first()
        assert agent is not None
        response = self.client.post(reverse('delete_agent', kwargs={'agent_id': agent.id}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Agent.objects.filter(pk=agent.id).exists())

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/delete/', count=1)]
    )
    def test_delete_agent_rejects_other_users_agent(self) -> None:
        other_agent = create_from_example(self.other, 'clock-assistant', identifier='protected-agent')
        self.client.force_login(self.user)
        response = self.client.post(reverse('delete_agent', kwargs={'agent_id': other_agent.id}))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Agent.objects.filter(pk=other_agent.id).exists())
