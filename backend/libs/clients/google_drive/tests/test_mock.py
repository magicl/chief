# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Protocol and authorization tests for the deterministic Google Drive mock."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.clients.google_drive.errors import (
    GoogleDriveConfigError,
    GoogleDriveInvalidCursorError,
    GoogleDriveOutsideRootError,
)
from libs.clients.google_drive.mock import MockGoogleDriveClient
from libs.clients.google_drive.protocol import GoogleDriveClientProtocol

from olib.py.django.test.cases import OTestCase

# Cursor internals are part of the mock-parity contract under test.
# pylint: disable=protected-access


def _config() -> dict[str, Any]:
    """Return two configured aliases in intentionally non-alphabetic order."""
    return {
        'roots': [
            {'id': 'zeta', 'file_id': 'root'},
            {'id': 'alpha', 'file_id': 'single'},
        ]
    }


class TestMockGoogleDriveClient(OTestCase):
    """Require deterministic behavior compatible with the production protocol."""

    def _client(self, *, instance_id: str = 'drive') -> MockGoogleDriveClient:
        """Seed a representative metadata tree without content."""
        client = MockGoogleDriveClient(
            token_supplier=lambda: None,
            config=_config(),
            instance_id=instance_id,
        )
        client.seed_item('root', name='Root', kind='folder')
        client.seed_item('folder-b', name='Beta', kind='folder', parent_refs=('root',))
        client.seed_item('folder-a', name='alpha', kind='folder', parent_refs=('root',))
        client.seed_item('file', name='Alpha report', kind='file', parent_refs=('folder-a',))
        client.seed_item('shortcut', name='Alpha link', kind='shortcut', parent_refs=('root',))
        client.seed_item('single', name='Single', kind='file')
        return client

    def test_implements_protocol_and_lists_configured_roots_by_alias(self) -> None:
        """Expose only configured roots in deterministic alias order."""
        client = self._client()
        self.assertIsInstance(client, GoogleDriveClientProtocol)
        result = client.list_roots()
        self.assertEqual([item['root'] for item in result['items']], ['alpha', 'zeta'])
        self.assertEqual([item['id'] for item in result['items']], ['single', 'root'])

    def test_lists_direct_children_with_folder_then_name_order_and_pagination(self) -> None:
        """Sort folders before other direct children and paginate with bound cursors."""
        client = self._client()
        first = client.list_folder(root='zeta', max_results=1)
        self.assertEqual([item['id'] for item in first['items']], ['folder-a'])
        self.assertIsNotNone(first['next_cursor'])
        encoded_state = client._decode_cursor(
            first['next_cursor'],
            root=client._config.roots[0],
            operation='list_folder',
            query=None,
            kinds=(),
            resource_ref='root',
            resource_default=True,
        )
        assert encoded_state is not None
        self.assertEqual(client._provider_state(encoded_state), (None, ['folder-b', 'shortcut']))
        self.assertNotIn('Beta', encoded_state)
        self.assertNotIn('Alpha link', encoded_state)

        second = client.list_folder(root='zeta', cursor=first['next_cursor'], max_results=1)
        third = client.list_folder(root='zeta', cursor=second['next_cursor'], max_results=1)
        self.assertEqual([item['id'] for item in second['items']], ['folder-b'])
        self.assertEqual([item['id'] for item in third['items']], ['shortcut'])
        self.assertIsNone(third['next_cursor'])

    def test_list_pending_ids_prevent_reordering_skips_and_duplicates(self) -> None:
        """Resume original IDs exactly once when metadata changes provider ordering."""
        client = self._client()
        first = client.list_folder(root='zeta', max_results=1)
        client.seed_item('shortcut', name='0-first', kind='folder', parent_refs=('root',))

        second = client.list_folder(root='zeta', cursor=first['next_cursor'], max_results=1)
        third = client.list_folder(root='zeta', cursor=second['next_cursor'], max_results=1)

        returned = first['items'] + second['items'] + third['items']
        self.assertEqual([item['id'] for item in returned], ['folder-a', 'folder-b', 'shortcut'])
        self.assertEqual(len(returned), len({item['id'] for item in returned}))

    def test_list_pending_item_rechecks_exact_direct_parent(self) -> None:
        """Reject a buffered child moved below another authorized folder."""
        client = self._client()
        first = client.list_folder(root='zeta', max_results=1)
        client.seed_item('folder-b', name='Beta', kind='folder', parent_refs=('folder-a',))

        with self.assertRaises(GoogleDriveOutsideRootError):
            client.list_folder(root='zeta', cursor=first['next_cursor'], max_results=1)

    def test_special_root_locator_maps_to_distinct_canonical_seeded_id(self) -> None:
        """Model Drive's special root locator without requiring an item literally named root."""
        client = MockGoogleDriveClient(
            token_supplier=lambda: None,
            config={'roots': [{'id': 'mine', 'file_id': 'root'}]},
            instance_id='drive',
        )
        client.seed_item('canonical-root-id', name='My Drive', kind='folder')
        client.seed_root('mine', canonical_item_id='canonical-root-id')
        client.seed_item(
            'child',
            name='Child',
            kind='file',
            parent_refs=('canonical-root-id',),
        )
        client.seed_item(
            'second-child',
            name='Second',
            kind='file',
            parent_refs=('canonical-root-id',),
        )

        self.assertEqual(client.list_roots()['items'][0]['id'], 'canonical-root-id')
        first = client.list_folder(root='mine', max_results=1)
        resumed = client.list_folder(root='mine', cursor=first['next_cursor'], max_results=1)
        self.assertEqual([item['id'] for item in first['items']], ['child'])
        self.assertEqual([item['id'] for item in resumed['items']], ['second-child'])
        self.assertEqual(
            client.get_metadata(root='mine', item_ref='canonical-root-id')['item']['id'],
            'canonical-root-id',
        )

    def test_seed_root_rejects_non_special_or_non_distinct_mapping(self) -> None:
        """Keep canonical root seeding specific to Drive's distinct root locator behavior."""
        client = self._client()
        with self.assertRaises(GoogleDriveConfigError):
            client.seed_root('alpha', canonical_item_id='single')
        with self.assertRaises(GoogleDriveConfigError):
            client.seed_root('zeta', canonical_item_id='root')

    def test_empty_folder_reference_is_invalid(self) -> None:
        """Match production validation instead of treating an empty reference as omitted."""
        client = self._client()
        with self.assertRaises(GoogleDriveConfigError):
            client.list_folder(root='zeta', folder_ref='')

    def test_public_input_bounds_match_production(self) -> None:
        """Reject oversized aliases, references, queries, and kind collections."""
        client = self._client()
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.list_folder(root='x' * 257),
            lambda: client.list_folder(root='zeta', folder_ref='x' * 4_097),
            lambda: client.get_metadata(root='zeta', item_ref='x' * 4_097),
            lambda: client.search(root='zeta', query='x' * 4_097),
            lambda: client.search(root='zeta', query='x', kinds=('file', 'folder', 'file')),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(GoogleDriveConfigError):
                operation()

    def test_list_cursor_is_bound_to_selected_folder(self) -> None:
        """Reject reuse of one mock list cursor against another folder."""
        client = self._client()
        client.seed_item('child-a', name='A1', kind='file', parent_refs=('folder-a',))
        client.seed_item('child-b', name='A2', kind='file', parent_refs=('folder-a',))
        first = client.list_folder(root='zeta', folder_ref='folder-a', max_results=1)

        with self.assertRaises(GoogleDriveInvalidCursorError):
            client.list_folder(
                root='zeta',
                folder_ref='folder-b',
                cursor=first['next_cursor'],
                max_results=1,
            )

    def test_get_metadata_uses_current_ancestry_for_moves_and_cycles(self) -> None:
        """Reject a formerly authorized item after a move or cyclic parent update."""
        client = self._client()
        self.assertEqual(client.get_metadata(root='zeta', item_ref='file')['item']['id'], 'file')
        client.seed_item('file', name='Alpha report', kind='file', parent_refs=('outside',))
        with self.assertRaises(GoogleDriveOutsideRootError):
            client.get_metadata(root='zeta', item_ref='file')
        client.seed_item('outside', name='Outside', kind='folder', parent_refs=('file',))
        with self.assertRaises(GoogleDriveOutsideRootError):
            client.get_metadata(root='zeta', item_ref='file')

    def test_search_preserves_deterministic_name_order_and_kind_filters(self) -> None:
        """Search names without content and distinguish files, folders, and shortcuts."""
        client = self._client()
        all_items = client.search(root='zeta', query='alpha')
        self.assertEqual([item['id'] for item in all_items['items']], ['folder-a', 'shortcut', 'file'])
        files = client.search(root='zeta', query='alpha', kinds=('file',))
        self.assertEqual([item['id'] for item in files['items']], ['file'])
        folders = client.search(root='zeta', query='alpha', kinds=('folder',))
        self.assertEqual([item['id'] for item in folders['items']], ['folder-a'])
        with self.assertRaises(GoogleDriveConfigError):
            client.search(root='zeta', query='alpha', kinds=('shortcut',))

    def test_search_cursor_is_bound_and_resume_reauthorizes_moved_items(self) -> None:
        """Reject mismatched cursors and apply current ancestry after resume."""
        client = self._client()
        first = client.search(root='zeta', query='alpha', max_results=1)
        cursor = first['next_cursor']
        self.assertIsNotNone(cursor)
        with self.assertRaises(GoogleDriveInvalidCursorError):
            self._client(instance_id='other').search(
                root='zeta',
                query='alpha',
                cursor=cursor,
                max_results=1,
            )
        with self.assertRaises(GoogleDriveInvalidCursorError):
            client.search(root='zeta', query='different', cursor=cursor, max_results=1)
        client.seed_item('file', name='Alpha report', kind='file', parent_refs=('outside',))
        resumed = client.search(root='zeta', query='alpha', cursor=cursor, max_results=5)
        self.assertNotIn('file', [item['id'] for item in resumed['items']])

    def test_file_root_rejects_list_and_search_but_supports_metadata(self) -> None:
        """Match production behavior for an individually configured file."""
        client = self._client()
        self.assertEqual(client.get_metadata(root='alpha', item_ref='single')['item']['id'], 'single')
        with self.assertRaises(GoogleDriveConfigError):
            client.list_folder(root='alpha')
        with self.assertRaises(GoogleDriveConfigError):
            client.search(root='alpha', query='single')

    def test_seeded_results_contain_metadata_only_and_never_follow_shortcuts(self) -> None:
        """Exclude content and shortcut targets from all normalized mock records."""
        client = self._client()
        shortcut = client.get_metadata(root='zeta', item_ref='shortcut')['item']
        self.assertEqual(shortcut['kind'], 'shortcut')
        self.assertNotIn('content', shortcut)
        self.assertNotIn('target', repr(shortcut))
