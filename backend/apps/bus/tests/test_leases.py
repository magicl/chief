# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import MagicMock, patch

from apps.bus import leases as lease_service
from apps.bus.leases import lease_key, release_lease, try_acquire_lease
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


@override_settings(CACHE_PREFIX='test:')
class TestLeases(OTestCase):
    """Verify cache-prefixed, owner-safe Redis leases."""

    def test_lease_key_is_cache_prefixed(self) -> None:
        """Build a namespaced lease key from the configured cache prefix."""
        self.assertEqual(lease_key('local-sync'), 'test:lease:local-sync')

    @patch('apps.bus.leases.sync_client')
    def test_try_acquire_lease_uses_atomic_set(self, mock_sync: MagicMock) -> None:
        """Acquire only when Redis accepts the NX lease with its TTL."""
        client = mock_sync.return_value
        client.set.side_effect = [True, None]

        self.assertTrue(try_acquire_lease('local-sync', 'owner-a', ttl_seconds=30))
        self.assertFalse(try_acquire_lease('local-sync', 'owner-b', ttl_seconds=30))
        self.assertEqual(
            client.set.call_args_list,
            [
                (('test:lease:local-sync', 'owner-a'), {'nx': True, 'ex': 30}),
                (('test:lease:local-sync', 'owner-b'), {'nx': True, 'ex': 30}),
            ],
        )

    @patch('apps.bus.leases.sync_client')
    def test_renew_lease_extends_only_matching_owner_atomically(self, mock_sync: MagicMock) -> None:
        """Extend the TTL only while the caller's token still owns the lease."""
        client = mock_sync.return_value
        leases = {'test:lease:local-sync': ('owner-a', 30)}

        def eval_script(
            _script: str,
            _key_count: int,
            key: str,
            token: str,
            ttl_seconds: int,
        ) -> int:
            """Model Redis's atomic token comparison and conditional expiry."""
            current = leases.get(key)
            if current is None or current[0] != token:
                return 0
            leases[key] = (token, ttl_seconds)
            return 1

        client.eval.side_effect = eval_script

        self.assertTrue(lease_service.renew_lease('local-sync', 'owner-a', ttl_seconds=45))
        self.assertFalse(lease_service.renew_lease('local-sync', 'owner-b', ttl_seconds=60))
        self.assertEqual(leases, {'test:lease:local-sync': ('owner-a', 45)})

        script, key_count, key, token, ttl_seconds = client.eval.call_args_list[0].args
        self.assertIn("redis.call('get', KEYS[1]) == ARGV[1]", script)
        self.assertIn("redis.call('expire', KEYS[1], ARGV[2])", script)
        self.assertEqual(
            (key_count, key, token, ttl_seconds),
            (1, 'test:lease:local-sync', 'owner-a', 45),
        )
        client.get.assert_not_called()
        client.expire.assert_not_called()

    @patch('apps.bus.leases.sync_client')
    def test_release_lease_removes_matching_owner_atomically(self, mock_sync: MagicMock) -> None:
        """Release a lease through one compare-and-delete Lua evaluation."""
        client = mock_sync.return_value
        leases = {'test:lease:local-sync': 'owner-a'}

        def eval_script(_script: str, _key_count: int, key: str, token: str) -> int:
            """Model Redis's atomic token comparison and conditional deletion."""
            if leases.get(key) != token:
                return 0
            del leases[key]
            return 1

        client.eval.side_effect = eval_script

        self.assertTrue(release_lease('local-sync', 'owner-a'))
        self.assertEqual(leases, {})

        client.eval.assert_called_once()
        script, key_count, key, token = client.eval.call_args.args
        self.assertIn("redis.call('get', KEYS[1]) == ARGV[1]", script)
        self.assertIn("redis.call('del', KEYS[1])", script)
        self.assertEqual((key_count, key, token), (1, 'test:lease:local-sync', 'owner-a'))
        client.get.assert_not_called()
        client.delete.assert_not_called()

    @patch('apps.bus.leases.sync_client')
    def test_stale_owner_cannot_remove_replacement_lease(self, mock_sync: MagicMock) -> None:
        """Keep a replacement lease when a stale owner attempts release."""
        client = mock_sync.return_value
        leases = {'test:lease:local-sync': 'owner-b'}

        def eval_script(_script: str, _key_count: int, key: str, token: str) -> int:
            """Model Redis's atomic token comparison and conditional deletion."""
            if leases.get(key) != token:
                return 0
            del leases[key]
            return 1

        client.eval.side_effect = eval_script

        self.assertFalse(release_lease('local-sync', 'owner-a'))
        self.assertEqual(leases, {'test:lease:local-sync': 'owner-b'})
        client.get.assert_not_called()
        client.delete.assert_not_called()
