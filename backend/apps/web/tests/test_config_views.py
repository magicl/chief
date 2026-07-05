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
from libs.agent_specs import load_example, load_example_text

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
        self.assertContains(response, 'id: clock')
        self.assertContains(response, 'config-sidebar')
        self.assertContains(response, 'id="agent-identifier"')
        self.assertContains(response, 'value="cfg-agent"')

    def test_save_renames_agent_identifier(self) -> None:
        url = reverse('agent_config_save', kwargs={'agent_id': self.agent.id})
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        response = self.client.post(
            url,
            {'spec_yaml': spec_yaml, 'identifier': 'renamed-agent'},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.identifier, 'renamed-agent')

    def test_save_rejects_duplicate_identifier(self) -> None:
        create_from_example(self.user, 'clock-assistant', identifier='taken-name')
        url = reverse('agent_config_save', kwargs={'agent_id': self.agent.id})
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        response = self.client.post(
            url,
            {'spec_yaml': spec_yaml, 'identifier': 'taken-name'},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertEqual(payload['errors'][0]['path'], 'identifier')

    def test_create_mutate_without_agent(self) -> None:
        spec_yaml = load_example_text('minimal')
        mutation = json.dumps({'action': 'add_tool', 'id': 'clock', 'type': 'clock', 'allow': ['now']})
        response = self.client.post(
            reverse('agent_create_mutate'),
            {'spec_yaml': spec_yaml, 'mutation': mutation},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertIn('yaml', payload)
        self.assertIn('id: clock', payload['yaml'])

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

    def test_mutate_add_tool_returns_yaml(self) -> None:
        url = reverse('agent_config_mutate', kwargs={'agent_id': self.agent.id})
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        mutation = json.dumps({'action': 'add_tool', 'id': 'queue', 'type': 'queue', 'allow': ['take']})
        response = self.client.post(url, {'spec_yaml': spec_yaml, 'mutation': mutation})
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertIn('yaml', payload)
        self.assertIn('id: queue', payload['yaml'])

    def test_config_page_404_for_other_user(self) -> None:
        other = get_user_model().objects.create_user(username='other', password='secret')
        other_agent = create_from_example(other, 'clock-assistant', identifier='other-agent')
        url = reverse('agent_config', kwargs={'agent_id': other_agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_create_from_yaml_via_web(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('queue-echo'))
        response = self.client.post(
            reverse('agent_create'),
            {'spec_yaml': spec_yaml, 'identifier': 'queue-web'},
        )
        self.assertEqual(response.status_code, 302)
        agent = Agent.objects.get(identifier='queue-web')
        config = agent.current_config
        assert config is not None
        self.assertEqual(len(config.get_spec().queues), 1)

    def test_import_errors_rendered_on_create_page(self) -> None:
        response = self.client.post(reverse('agent_create'), {'spec_yaml': 'not: [valid'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Could not create agent')
