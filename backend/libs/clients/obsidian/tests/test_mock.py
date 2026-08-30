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
    ObsidianUnavailableError,
)
from libs.clients.obsidian.mock import MockObsidianVaultClient
from libs.clients.obsidian.protocol import ObsidianVaultClientProtocol

from olib.py.django.test.cases import OTestCase


class TestMockObsidianVaultClient(OTestCase):
    def test_implements_protocol(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        protocol_client: ObsidianVaultClientProtocol = client
        self.assertIsInstance(protocol_client, ObsidianVaultClientProtocol)

    def test_get_status_unseeded_vault_is_all_false(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        self.assertEqual(
            client.get_status(vault_id='Unknown'),
            {
                'vault_id': 'Unknown',
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': False,
            },
        )

    def test_get_status_reports_not_ready_without_raising(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=False)
        self.assertEqual(
            client.get_status(vault_id='Personal'),
            {
                'vault_id': 'Personal',
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': True,
            },
        )

    def test_get_status_can_report_dead_sync_child(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.set_sync_process_alive('Personal', False)
        self.assertFalse(client.get_status(vault_id='Personal')['sync_process_alive'])

    def test_ensure_vaults_records_call_and_binds_roots(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        bindings = [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}]

        client.ensure_vaults(bindings)

        self.assertEqual(client.ensure_calls, [bindings])
        client.set_ready('Personal', True)
        self.assertEqual(client.list_dir(vault_id='Personal', path='Journal'), [])

    def test_reads_serve_partial_content_before_first_sync_completes(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
        client.seed_file('Personal', 'Journal/a.md', 'partial')

        self.assertEqual(client.list_dir(vault_id='Personal', path='Journal'), ['a.md'])
        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'partial')
        self.assertFalse(client.get_status(vault_id='Personal')['ready'])

    def test_reads_before_ready_still_enforce_configured_roots(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
        client.seed_file('Personal', 'Other/a.md', 'partial')

        with self.assertRaises(ObsidianOutsideRootError):
            client.read_text(vault_id='Personal', path='Other/a.md')
        with self.assertRaises(ObsidianOutsideRootError):
            client.list_dir(vault_id='Personal', path='Journalism')

    def test_reads_before_ready_report_missing_files_as_not_found(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianNotFoundError):
            client.read_text(vault_id='Personal', path='Journal/missing.md')

    def test_writes_raise_sync_pending_until_ready(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianSyncPendingError):
            client.write_text(vault_id='Personal', path='Journal/a.md', content='hello')
        with self.assertRaises(ObsidianSyncPendingError):
            client.append_text(vault_id='Personal', path='Journal/a.md', content='hello')

        client.set_ready('Personal', True)
        client.write_text(vault_id='Personal', path='Journal/a.md', content='hello')
        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'hello')

    def test_writes_before_ready_report_sync_pending_ahead_of_root_scope(self) -> None:
        """Readiness outranks root scope for mutations, as in the vault service's write path."""
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianSyncPendingError):
            client.write_text(vault_id='Personal', path='Other/a.md', content='hello')
        with self.assertRaises(ObsidianSyncPendingError):
            client.append_text(vault_id='Personal', path='Other/a.md', content='hello')

    def test_writes_when_ready_outside_roots_raise_outside_root(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])

        with self.assertRaises(ObsidianOutsideRootError):
            client.write_text(vault_id='Personal', path='Other/a.md', content='hello')
        with self.assertRaises(ObsidianOutsideRootError):
            client.append_text(vault_id='Personal', path='Other/a.md', content='hello')

    def test_writes_to_unseeded_vault_raise_not_found_before_readiness(self) -> None:
        """Binding resolution comes first, so an unknown vault never looks merely unsynced."""
        client = MockObsidianVaultClient(agent_id='agent-1')

        with self.assertRaises(ObsidianNotFoundError):
            client.write_text(vault_id='Ghost', path='Journal/a.md', content='hello')
        with self.assertRaises(ObsidianNotFoundError):
            client.append_text(vault_id='Ghost', path='Journal/a.md', content='hello')

    def test_hard_failure_makes_every_seeded_file_operation_unavailable(self) -> None:
        """A seeded bound vault reports hard failure before readiness or root scope."""
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=False)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
        client.set_failed('Personal', True)

        operations = (
            lambda: client.list_dir(vault_id='Personal', path='Other'),
            lambda: client.read_text(vault_id='Personal', path='Other/a.md'),
            lambda: client.write_text(vault_id='Personal', path='Other/a.md', content='write'),
            lambda: client.append_text(vault_id='Personal', path='Other/a.md', content='append'),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ObsidianUnavailableError):
                operation()

        self.assertEqual(
            client.get_status(vault_id='Personal'),
            {
                'vault_id': 'Personal',
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': False,
            },
        )

    def test_hard_failure_can_reset_and_recover_through_normal_readiness(self) -> None:
        """Clearing failure restores partial reads and the existing write readiness gate."""
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=False)
        client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
        client.seed_file('Personal', 'Journal/a.md', 'partial')
        client.set_failed('Personal', True)
        client.set_failed('Personal', False)

        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'partial')
        with self.assertRaises(ObsidianSyncPendingError):
            client.write_text(vault_id='Personal', path='Journal/a.md', content='blocked')

        client.set_ready('Personal', True)
        client.write_text(vault_id='Personal', path='Journal/a.md', content='recovered')
        self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'recovered')

    def test_unseeded_vault_remains_not_found_when_failure_control_is_unused(self) -> None:
        """Hard-failure support does not change unknown-vault precedence."""
        client = MockObsidianVaultClient(agent_id='agent-1')

        operations = (
            lambda: client.list_dir(vault_id='Ghost', path='Journal'),
            lambda: client.read_text(vault_id='Ghost', path='Journal/a.md'),
            lambda: client.write_text(vault_id='Ghost', path='Journal/a.md', content='write'),
            lambda: client.append_text(vault_id='Ghost', path='Journal/a.md', content='append'),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ObsidianNotFoundError):
                operation()

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
