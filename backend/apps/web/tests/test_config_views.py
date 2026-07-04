# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent config web endpoints."""

import json

from apps.agents.models import Agent
from apps.agents.services.config_commands import create_from_example
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class AgentConfigWebTests(OTestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = get_user_model().objects.create_user(username='cfg-ui', password='secret')
        self.client.login(username='cfg-ui', password='secret')
        self.agent = create_from_example(self.user, 'clock-assistant', identifier='cfg-agent')

    def test_config_page_requires_login(self) -> None:
        anon = Client()
        url = reverse('agent_config', kwargs={'agent_id': self.agent.id})
        response = anon.get(url)
        self.assertEqual(response.status_code, 302)

    def test_config_page_renders_editor(self) -> None:
        url = reverse('agent_config', kwargs={'agent_id': self.agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'config-editor')

    def test_save_invalid_yaml_returns_json_errors(self) -> None:
        url = reverse('agent_config_save', kwargs={'agent_id': self.agent.id})
        response = self.client.post(
            url,
            {'spec_yaml': 'broken: yaml: ['},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertIn('errors', payload)

    def test_save_valid_yaml_persists_new_revision(self) -> None:
        url = reverse('agent_config_save', kwargs={'agent_id': self.agent.id})
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        before = Agent.objects.get(pk=self.agent.pk).current_config_id
        response = self.client.post(url, {'spec_yaml': spec_yaml}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertNotEqual(self.agent.current_config_id, before)
