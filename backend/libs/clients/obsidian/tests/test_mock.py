# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the in-memory Obsidian vault test client."""

from __future__ import annotations

from libs.clients.obsidian.errors import (
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
)
from libs.clients.obsidian.mock import MockObsidianVaultClient
from libs.clients.obsidian.protocol import ObsidianVaultClientProtocol

from olib.py.django.test.cases import OTestCase


class TestMockObsidianVaultClient(OTestCase):
    def test_implements_protocol(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        protocol_client: ObsidianVaultClientProtocol = client
        self.assertIsInstance(protocol_client, ObsidianVaultClientProtocol)

    def test_ensure_vaults_records_call_and_binds_roots(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        bindings = [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}]

        client.ensure_vaults(bindings)

        self.assertEqual(client.ensure_calls, [bindings])
        client.set_ready('Personal', True)
        self.assertEqual(client.list_dir(vault_id='Personal', path='Journal'), [])

    def test_file_ops_stall_with_sync_pending_until_ready(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianSyncPendingError):
            client.read_text(vault_id='Personal', path='Journal/a.md')

        client.set_ready('Personal', True)
        client.write_text(vault_id='Personal', path='Journal/a.md', content='hello')
        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'hello')

    def test_write_then_append_accumulates_content(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        client.write_text(vault_id='Personal', path='Journal/a.md', content='hello')
        client.append_text(vault_id='Personal', path='Journal/a.md', content=' world')

        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'hello world')

    def test_list_dir_returns_direct_children_only(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
        client.write_text(vault_id='Personal', path='Journal/a.md', content='a')
        client.write_text(vault_id='Personal', path='Journal/sub/b.md', content='b')

        self.assertEqual(client.list_dir(vault_id='Personal', path='Journal'), ['a.md', 'sub'])

    def test_read_missing_file_raises_not_found(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianNotFoundError):
            client.read_text(vault_id='Personal', path='Journal/missing.md')

    def test_unseeded_vault_raises_not_found(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')

        with self.assertRaises(ObsidianNotFoundError):
            client.read_text(vault_id='Ghost', path='Journal/a.md')

    def test_path_outside_bound_roots_raises_outside_root(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianOutsideRootError):
            client.read_text(vault_id='Personal', path='Other/a.md')

    def test_root_match_is_segment_aware(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianOutsideRootError):
            client.read_text(vault_id='Personal', path='Journalism/a.md')

    def test_release_vaults_clears_root_bindings(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        client.release_vaults()

        self.assertTrue(client.released)
        with self.assertRaises(ObsidianOutsideRootError):
            client.read_text(vault_id='Personal', path='Journal/a.md')
