# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import unittest

from obsidian_vault.bindings import SyncPendingError, VaultBindingStore


class TestVaultBindingStore(unittest.TestCase):
    def test_ensure_then_lookup_roots(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [
                {
                    'vault_id': 'Personal',
                    'roots': ['Journal'],
                    'credential': {'auth_token': 'tok', 'encryption_password': None},
                }
            ],
        )
        binding = store.get_binding('agent-1', vault_id='Personal')
        self.assertEqual(binding.roots, ['Journal'])
        self.assertFalse(binding.ready)

    def test_mark_ready_clears_sync_pending(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        store.mark_vault_ready('Personal')
        self.assertTrue(store.require_ready('agent-1', 'Personal'))

    def test_require_ready_raises_while_pending(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        with self.assertRaises(SyncPendingError):
            store.require_ready('agent-1', 'Personal')

    def test_refcount_teardown_when_last_agent_releases(self) -> None:
        store = VaultBindingStore()
        for agent in ('a', 'b'):
            store.ensure_agent(
                agent,
                [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
            )
        released = store.release_agent('a')
        self.assertEqual(released, [])
        released = store.release_agent('b')
        self.assertEqual(released, ['Personal'])

    def test_has_references_reflects_active_bindings(self) -> None:
        store = VaultBindingStore()
        self.assertFalse(store.has_references('Personal'))
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        self.assertTrue(store.has_references('Personal'))
        store.release_agent('agent-1')
        self.assertFalse(store.has_references('Personal'))

    def test_release_at_zero_refcount_clears_ready_for_recreate(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        store.mark_vault_ready('Personal')
        store.release_agent('agent-1')

        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        with self.assertRaises(SyncPendingError):
            store.require_ready('agent-1', 'Personal')

    def test_replace_all_agents_cold_start_returns_needs_start(self) -> None:
        """Empty store + snapshot should need a supervisor start for each vault."""
        store = VaultBindingStore()
        needs_start, released = store.replace_all_agents(
            [
                {
                    'agent_id': 'agent-1',
                    'bindings': [
                        {
                            'vault_id': 'Personal',
                            'roots': ['Journal'],
                            'credential': {'auth_token': 'tok'},
                        }
                    ],
                }
            ]
        )
        self.assertEqual(needs_start, ['Personal'])
        self.assertEqual(released, [])
        binding = store.get_binding('agent-1', 'Personal')
        self.assertEqual(binding.roots, ['Journal'])
        self.assertFalse(binding.ready)

    def test_replace_all_agents_preserves_ready_for_still_referenced_vaults(self) -> None:
        """Vaults that remain referenced keep readiness across a snapshot replace."""
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        store.mark_vault_ready('Personal')
        needs_start, released = store.replace_all_agents(
            [
                {
                    'agent_id': 'agent-1',
                    'bindings': [
                        {
                            'vault_id': 'Personal',
                            'roots': ['Journal'],
                            'credential': {'auth_token': 'tok'},
                        }
                    ],
                }
            ]
        )
        self.assertEqual(needs_start, [])
        self.assertEqual(released, [])
        self.assertTrue(store.require_ready('agent-1', 'Personal'))

    def test_replace_all_agents_releases_removed_vaults(self) -> None:
        """Vaults absent from the new snapshot are returned as released."""
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        needs_start, released = store.replace_all_agents([])
        self.assertEqual(needs_start, [])
        self.assertEqual(released, ['Personal'])
        self.assertFalse(store.has_references('Personal'))
