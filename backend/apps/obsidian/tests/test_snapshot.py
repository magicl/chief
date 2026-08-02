# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the internal vault-bindings snapshot HTTP endpoint."""

from __future__ import annotations

import json
import logging

from apps.agents.models import Agent, AgentConfig, AgentStatus
from apps.keys.services.commands import upsert_user_named
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_VAULT_CONFIG = {'vault': 'Personal', 'roots': ['Journal']}
_REQUEST_LOGGER = 'django.request'


def _spec(*tools: ToolInstance) -> AgentConfigSpec:
    """Build a minimal agent spec with the given tools."""
    return AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_', tools=list(tools))


class TestVaultBindingsSnapshot(OTestCase):
    @override_settings(OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems(
        [ExpectLogItem(_REQUEST_LOGGER, logging.WARNING, r'Unauthorized: /internal/obsidian/vault-bindings/', count=2)]
    )
    def test_requires_bearer_token(self) -> None:
        """Missing/invalid bearer yields 401."""
        url = reverse('obsidian_vault_bindings_snapshot')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer wrong')
        self.assertEqual(response.status_code, 401)

    @override_settings(OBSIDIAN_VAULT_TOKEN='')
    @expectLogItems(
        [
            ExpectLogItem(
                _REQUEST_LOGGER, logging.ERROR, r'Service Unavailable: /internal/obsidian/vault-bindings/', count=1
            )
        ]
    )
    def test_unset_token_returns_unavailable(self) -> None:
        """When the vault token is unset, the snapshot endpoint is unavailable."""
        url = reverse('obsidian_vault_bindings_snapshot')
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer anything')
        self.assertEqual(response.status_code, 503)

    @override_settings(OBSIDIAN_VAULT_TOKEN='service-token')
    def test_returns_active_agent_bindings_with_resolved_credentials(self) -> None:
        """Active agents with valid obsidian tools appear with Sync secrets resolved."""
        user = get_user_model().objects.create_user(username='snap-user', password='x')
        agent = Agent.objects.create(
            user_id=user.pk,
            name='Snap agent',
            identifier='snap-agent',
            status=AgentStatus.ACTIVE,
        )
        upsert_user_named(
            user.pk,
            'obsidian-personal',
            'obsidian',
            json.dumps({'auth_token': 'sync-tok', 'encryption_password': 'pw'}),
        )
        spec = _spec(
            ToolInstance(
                id='vault',
                type='obsidian',
                credential_ref='obsidian-personal',
                config=_VAULT_CONFIG,
            )
        )
        config = AgentConfig.objects.create(
            agent=agent,
            source_rev='v1',
            spec_version=1,
            spec=spec.model_dump(mode='json'),
        )
        agent.current_config = config
        agent.save(update_fields=['current_config'])

        url = reverse('obsidian_vault_bindings_snapshot')
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer service-token')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(
            payload['agents'],
            [
                {
                    'agent_id': str(agent.id),
                    'bindings': [
                        {
                            'vault_id': 'Personal',
                            'roots': ['Journal'],
                            'credential': {
                                'auth_token': 'sync-tok',
                                'encryption_password': 'pw',
                            },
                        }
                    ],
                }
            ],
        )

    @override_settings(OBSIDIAN_VAULT_TOKEN='service-token')
    def test_omits_agents_without_obsidian_bindings(self) -> None:
        """Agents lacking obsidian tools are omitted from the snapshot."""
        user = get_user_model().objects.create_user(username='snap-empty', password='x')
        agent = Agent.objects.create(
            user_id=user.pk,
            name='Empty agent',
            identifier='empty-agent',
            status=AgentStatus.ACTIVE,
        )
        spec = _spec()
        config = AgentConfig.objects.create(
            agent=agent,
            source_rev='v1',
            spec_version=1,
            spec=spec.model_dump(mode='json'),
        )
        agent.current_config = config
        agent.save(update_fields=['current_config'])

        url = reverse('obsidian_vault_bindings_snapshot')
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer service-token')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'agents': []})
