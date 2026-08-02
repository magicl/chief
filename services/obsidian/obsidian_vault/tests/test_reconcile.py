# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for Chief snapshot fetch/apply and startup retry reconcile."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import httpx
from obsidian_vault.bindings import VaultBindingStore
from obsidian_vault.reconcile import (
    apply_bindings_snapshot,
    fetch_bindings_snapshot,
    reconcile_bindings_from_chief,
)


class _FakeSupervisor:
    """Record ensure/stop calls for reconcile tests."""

    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.stopped: list[str] = []
        self.complete: set[str] = set()

    def ensure_vault(
        self,
        vault_id: str,
        *,
        auth_token: str,
        encryption_password: str | None,
    ) -> None:
        del auth_token, encryption_password
        self.ensured.append(vault_id)

    def stop_vault(self, vault_id: str) -> None:
        self.stopped.append(vault_id)

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        return vault_id in self.complete


class TestFetchBindingsSnapshot(unittest.TestCase):
    def test_fetch_returns_agents_list(self) -> None:
        """Successful Chief response yields the agents payload."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, '/internal/obsidian/vault-bindings/')
            self.assertEqual(request.headers.get('Authorization'), 'Bearer tok')
            return httpx.Response(200, json={'ok': True, 'agents': [{'agent_id': 'a', 'bindings': []}]})

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport, base_url='http://chief') as client:
            agents = fetch_bindings_snapshot(
                chief_internal_url='http://chief',
                token='tok',
                client=client,
            )
        self.assertEqual(agents, [{'agent_id': 'a', 'bindings': []}])


class TestApplyBindingsSnapshot(unittest.TestCase):
    def test_apply_starts_supervisors_and_marks_ready_when_synced(self) -> None:
        """Cold snapshot starts each vault and marks ready when sync already complete."""
        store = VaultBindingStore()
        supervisor = _FakeSupervisor()
        supervisor.complete.add('Personal')
        agents = [
            {
                'agent_id': 'agent-1',
                'bindings': [
                    {
                        'vault_id': 'Personal',
                        'roots': ['Journal'],
                        'credential': {'auth_token': 'sync-tok'},
                    }
                ],
            }
        ]

        needs_start, released = apply_bindings_snapshot(store, supervisor, agents)

        self.assertEqual(needs_start, ['Personal'])
        self.assertEqual(released, [])
        self.assertEqual(supervisor.ensured, ['Personal'])
        self.assertTrue(store.require_ready('agent-1', 'Personal'))

    def test_apply_stops_released_vaults(self) -> None:
        """Vaults dropped from the snapshot are stopped when refcount hits zero."""
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        supervisor = _FakeSupervisor()

        apply_bindings_snapshot(store, supervisor, [])

        self.assertEqual(supervisor.stopped, ['Personal'])


class TestReconcileRetries(unittest.TestCase):
    def test_reconcile_retries_until_success(self) -> None:
        """Transient Chief failures retry with backoff until a successful apply."""
        store = VaultBindingStore()
        supervisor = _FakeSupervisor()
        attempts = {'n': 0}
        sleeps: list[float] = []

        def fake_fetch(**_kwargs: Any) -> list[dict[str, Any]]:
            attempts['n'] += 1
            if attempts['n'] < 3:
                raise httpx.ConnectError('chief down')
            return [
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

        with mock.patch('obsidian_vault.reconcile.fetch_bindings_snapshot', fake_fetch):
            reconcile_bindings_from_chief(
                store=store,
                supervisor=supervisor,
                chief_internal_url='http://chief',
                token='tok',
                sleep=sleeps.append,
                initial_delay=0.5,
                max_delay=10.0,
            )

        self.assertEqual(attempts['n'], 3)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(supervisor.ensured, ['Personal'])
        self.assertEqual(store.get_binding('agent-1', 'Personal').roots, ['Journal'])
