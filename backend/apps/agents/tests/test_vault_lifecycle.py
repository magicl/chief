# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for ensuring/releasing Obsidian vault bindings on agent lifecycle."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from apps.agents.delete import delete_agent_for_user
from apps.agents.materialize import materialize_agent_config
from apps.agents.models import Agent, AgentConfig
from apps.agents.vault_lifecycle import release_obsidian_vaults, sync_obsidian_vaults
from apps.keys.services.commands import upsert_user_named
from django.contrib.auth import get_user_model
from django.test import override_settings
from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.obsidian.errors import ObsidianUnavailableError

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_LOGGER = 'apps.agents.vault_lifecycle'

_VAULT_CONFIG = {'vault': 'Personal', 'roots': ['Journal']}


def _spec(*tools: ToolInstance) -> AgentConfigSpec:
    """Build a minimal spec with the given obsidian (or other) tool instances."""
    return AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_', tools=list(tools))


class TestSyncObsidianVaults(OTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='vault-user', password='x')
        self.agent = Agent.objects.create(user_id=self.user.pk, name='Vault agent', identifier='vault-agent')

    @override_settings(OBSIDIAN_VAULT_URL='', OBSIDIAN_VAULT_TOKEN='')
    def test_noop_without_vault_url_even_with_obsidian_tools(self) -> None:
        """Skip vault sync entirely when the vault service is not configured."""
        upsert_user_named(self.user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'tok'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_noop_when_no_obsidian_tools(self) -> None:
        """Skip vault sync when the spec has no `obsidian` tool instances."""
        spec = _spec(ToolInstance(id='clock', type='clock', allow=['now']))

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_ensures_vault_with_resolved_credential(self) -> None:
        """Resolve the named credential and call ensure_vaults with the built binding."""
        upsert_user_named(
            self.user.pk,
            'obsidian-personal',
            'obsidian',
            json.dumps({'auth_token': 'sync-token', 'encryption_password': 'e2e-pw'}),
        )
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )

        client = MagicMock()
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client) as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_called_once_with(
            base_url='http://vault.internal',
            token='service-token',
            agent_id=str(self.agent.id),
        )
        client.ensure_vaults.assert_called_once_with(
            [
                {
                    'vault_id': 'Personal',
                    'roots': ['Journal'],
                    'credential': {'auth_token': 'sync-token', 'encryption_password': 'e2e-pw'},
                },
            ],
        )

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_omits_encryption_password_when_absent(self) -> None:
        """Build a binding without `encryption_password` when the credential JSON omits it."""
        upsert_user_named(self.user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'sync-token'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )

        client = MagicMock()
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client):
            sync_obsidian_vaults(self.agent, spec)

        bindings = client.ensure_vaults.call_args.args[0]
        self.assertEqual(
            bindings, [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'sync-token'}}]
        )

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems([ExpectLogItem(_LOGGER, logging.WARNING, r'credential unresolved', count=1)])
    def test_skips_tool_with_unresolvable_credential_ref(self) -> None:
        """Skip a tool instance whose credential_ref does not resolve, without raising."""
        spec = _spec(ToolInstance(id='vault', type='obsidian', credential_ref='missing-cred', config=_VAULT_CONFIG))

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems([ExpectLogItem(_LOGGER, logging.WARNING, r'credential is not valid JSON', count=1)])
    def test_skips_tool_with_malformed_credential_json(self) -> None:
        """Skip a tool instance whose resolved secret is not the expected JSON shape."""
        upsert_user_named(self.user.pk, 'obsidian-personal', 'obsidian', 'not-json')
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems([ExpectLogItem(_LOGGER, logging.WARNING, r'has invalid config', count=1)])
    def test_skips_tool_with_invalid_non_secret_config(self) -> None:
        """Skip a tool instance whose non-secret config fails validation."""
        upsert_user_named(self.user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'tok'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config={'vault': 'Personal'}),
        )

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            sync_obsidian_vaults(self.agent, spec)

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems([ExpectLogItem(_LOGGER, logging.WARNING, r'obsidian vault ensure failed', count=1)])
    def test_swallows_vault_service_failure(self) -> None:
        """Log and swallow a client failure instead of propagating it."""
        upsert_user_named(self.user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'tok'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )

        client = MagicMock()
        client.ensure_vaults.side_effect = ObsidianUnavailableError('boom')
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client):
            sync_obsidian_vaults(self.agent, spec)  # must not raise

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_ensures_multiple_vault_tool_instances(self) -> None:
        """Build one binding per `obsidian` tool instance and skip non-obsidian tools."""
        upsert_user_named(self.user.pk, 'obsidian-a', 'obsidian', json.dumps({'auth_token': 'tok-a'}))
        upsert_user_named(self.user.pk, 'obsidian-b', 'obsidian', json.dumps({'auth_token': 'tok-b'}))
        spec = _spec(
            ToolInstance(id='clock', type='clock', allow=['now']),
            ToolInstance(id='a', type='obsidian', credential_ref='obsidian-a', config={'vault': 'A', 'roots': ['x']}),
            ToolInstance(id='b', type='obsidian', credential_ref='obsidian-b', config={'vault': 'B', 'roots': ['y']}),
        )

        client = MagicMock()
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client):
            sync_obsidian_vaults(self.agent, spec)

        bindings = client.ensure_vaults.call_args.args[0]
        self.assertEqual(
            {(b['vault_id'], b['credential']['auth_token']) for b in bindings},
            {('A', 'tok-a'), ('B', 'tok-b')},
        )


class TestReleaseObsidianVaults(OTestCase):
    @override_settings(OBSIDIAN_VAULT_URL='', OBSIDIAN_VAULT_TOKEN='')
    def test_noop_without_vault_url(self) -> None:
        """Skip release entirely when the vault service is not configured."""
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            release_obsidian_vaults(uuid4())

        client_cls.assert_not_called()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_releases_vaults_for_agent_id(self) -> None:
        """Build a client scoped to the given agent id and call release_vaults."""
        agent_id = uuid4()
        client = MagicMock()
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client) as client_cls:
            release_obsidian_vaults(agent_id)

        client_cls.assert_called_once_with(
            base_url='http://vault.internal',
            token='service-token',
            agent_id=str(agent_id),
        )
        client.release_vaults.assert_called_once_with()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    @expectLogItems([ExpectLogItem(_LOGGER, logging.WARNING, r'obsidian vault release failed', count=1)])
    def test_swallows_vault_service_failure(self) -> None:
        """Log and swallow a client failure instead of propagating it."""
        client = MagicMock()
        client.release_vaults.side_effect = ObsidianUnavailableError('boom')
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client):
            release_obsidian_vaults(uuid4())  # must not raise


class TestMaterializeSchedulesObsidianSync(OTestCase):
    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_materialize_schedules_sync_after_commit(self) -> None:
        """Schedule `sync_obsidian_vaults` via on_commit, reloading the agent by id."""
        user = get_user_model().objects.create_user(username='mat-vault-user', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Mat vault agent', identifier='mat-vault-agent')
        upsert_user_named(user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'tok'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )
        config = AgentConfig.objects.create(
            agent=agent,
            source_rev='v1',
            spec_version=1,
            spec=spec.model_dump(mode='json'),
        )

        client = MagicMock()
        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient', return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                materialize_agent_config(agent, config, spec)

        client.ensure_vaults.assert_called_once()

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_materialize_does_not_call_vault_before_commit(self) -> None:
        """Never touch the vault client while still inside the open transaction."""
        user = get_user_model().objects.create_user(username='mat-vault-precommit', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Mat vault agent 2', identifier='mat-vault-agent-2')
        upsert_user_named(user.pk, 'obsidian-personal', 'obsidian', json.dumps({'auth_token': 'tok'}))
        spec = _spec(
            ToolInstance(id='vault', type='obsidian', credential_ref='obsidian-personal', config=_VAULT_CONFIG)
        )
        config = AgentConfig.objects.create(
            agent=agent,
            source_rev='v1',
            spec_version=1,
            spec=spec.model_dump(mode='json'),
        )

        with patch('apps.agents.vault_lifecycle.ObsidianVaultClient') as client_cls:
            with self.captureOnCommitCallbacks(execute=False):
                materialize_agent_config(agent, config, spec)
            client_cls.assert_not_called()


class TestDeleteSchedulesObsidianRelease(OTestCase):
    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_delete_schedules_release_after_commit(self) -> None:
        """Schedule `release_obsidian_vaults` for the deleted agent's captured id."""
        user = get_user_model().objects.create_user(username='del-vault-user', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Del vault agent', identifier='del-vault-agent')
        agent_id = agent.id

        with patch('apps.agents.vault_lifecycle.release_obsidian_vaults') as release:
            with self.captureOnCommitCallbacks(execute=True):
                delete_agent_for_user(user, agent_id)

        release.assert_called_once_with(agent_id)

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_release_runs_after_agent_row_is_gone(self) -> None:
        """Confirm the release callback fires only once the agent row is already deleted."""
        user = get_user_model().objects.create_user(username='del-vault-user-2', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Del vault agent 2', identifier='del-vault-agent-2')
        agent_id = agent.id

        seen_exists: list[bool] = []

        def _fake_release(released_agent_id: UUID) -> None:
            seen_exists.append(Agent.objects.filter(pk=released_agent_id).exists())

        with patch('apps.agents.vault_lifecycle.release_obsidian_vaults', side_effect=_fake_release):
            with self.captureOnCommitCallbacks(execute=True):
                delete_agent_for_user(user, agent_id)

        self.assertEqual(seen_exists, [False])
