# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Protocol and authorization tests for the deterministic Dropbox mock."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.clients.dropbox.errors import (
    DropboxConfigError,
    DropboxInvalidCursorError,
    DropboxOutsideRootError,
)
from libs.clients.dropbox.mock import MockDropboxClient
from libs.clients.dropbox.protocol import DropboxClientProtocol

from olib.py.django.test.cases import OTestCase


def _config() -> dict[str, Any]:
    """Return folder, file, and namespace root configuration."""
    return {
        'roots': [
            {'id': 'projects', 'path': '/Projects'},
            {'id': 'single', 'path': '/Single.txt'},
            {'id': 'all', 'path': '/'},
        ]
    }


class TestMockDropboxClient(OTestCase):
    """Require deterministic root-safe behavior matching the real client protocol."""

    def _client(self, *, instance_id: str = 'dbx') -> MockDropboxClient:
        """Seed representative Dropbox metadata without content or links."""
        client = MockDropboxClient(
            token_supplier=lambda: None,
            config=_config(),
            instance_id=instance_id,
        )
        client.seed_item('id:projects', path='/Projects', kind='folder')
        client.seed_item('id:b', path='/Projects/Beta', kind='folder')
        client.seed_item('id:a', path='/Projects/alpha', kind='folder')
        client.seed_item('id:file', path='/Projects/alpha/report.txt', kind='file', size=4, rev='r1')
        client.seed_item('id:single', path='/Single.txt', kind='file')
        return client

    def test_runtime_protocol_and_config_root_order(self) -> None:
        """Implement the public protocol and retain deterministic configuration order."""
        client = self._client()
        self.assertIsInstance(client, DropboxClientProtocol)
        roots = client.list_roots()
        self.assertEqual([item['root'] for item in roots['items']], ['projects', 'single', 'all'])
        self.assertEqual([item['id'] for item in roots['items']], ['id:projects', 'id:single', '/'])

    def test_list_folder_orders_paths_and_paginates_with_folder_binding(self) -> None:
        """List direct children by deterministic path/name order with bound cursors."""
        client = self._client()
        first = client.list_folder(root='projects', max_results=1)
        self.assertEqual([item['id'] for item in first['items']], ['id:a'])
        resumed = client.list_folder(root='projects', cursor=first['next_cursor'], max_results=2)
        self.assertEqual([item['id'] for item in resumed['items']], ['id:b'])
        with self.assertRaises(DropboxInvalidCursorError):
            client.list_folder(root='all', cursor=first['next_cursor'], max_results=1)

    def test_list_folder_buffer_preserves_order_and_reauthorizes_moves(self) -> None:
        """Resume buffered IDs in original order and reject a current outside path."""
        client = self._client()
        client.seed_item('id:c', path='/Projects/charlie', kind='folder')
        first = client.list_folder(root='projects', max_results=1)
        second = client.list_folder(root='projects', cursor=first['next_cursor'], max_results=1)
        third = client.list_folder(root='projects', cursor=second['next_cursor'], max_results=1)
        self.assertEqual(
            [item['id'] for page in (first, second, third) for item in page['items']],
            ['id:a', 'id:b', 'id:c'],
        )

        client = self._client()
        first = client.list_folder(root='projects', max_results=1)
        client.seed_item('id:b', path='/Projects2/Beta', kind='folder')
        with self.assertRaises(DropboxOutsideRootError):
            client.list_folder(root='projects', cursor=first['next_cursor'], max_results=1)

    def test_moves_and_segment_boundaries_are_reauthorized(self) -> None:
        """Reject moved items and sibling-prefix paths using authoritative segments."""
        client = self._client()
        self.assertEqual(client.get_metadata(root='projects', item_ref='id:file')['item']['id'], 'id:file')
        client.seed_item('id:file', path='/Projects2/report.txt', kind='file')
        with self.assertRaises(DropboxOutsideRootError):
            client.get_metadata(root='projects', item_ref='id:file')

    def test_public_input_bounds_match_production(self) -> None:
        """Reject oversized aliases, references, queries, and kind collections."""
        client = self._client()
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.list_folder(root='x' * 257),
            lambda: client.list_folder(root='projects', folder_ref='x' * 4_097),
            lambda: client.get_metadata(root='projects', item_ref='x' * 4_097),
            lambda: client.search(root='projects', query='x' * 1_001),
            lambda: client.search(root='projects', query='x', kinds=('file', 'folder', 'file')),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(DropboxConfigError):
                operation()

    def test_authoritative_unicode_path_lower_can_be_seeded_verbatim(self) -> None:
        """Model legacy Dropbox path_lower values without Python renormalization."""
        client = MockDropboxClient(
            token_supplier=lambda: None,
            config={'roots': [{'id': 'legacy', 'path': '/Legacy'}]},
            instance_id='dbx',
        )
        client.seed_item('id:root', path='/Legacy', path_lower='/ꙋ', kind='folder')
        client.seed_item('id:file', path='/Legacy/file', path_lower='/Ꙋ/file', kind='file')
        with self.assertRaises(DropboxOutsideRootError):
            client.get_metadata(root='legacy', item_ref='id:file')

    def test_root_locator_does_not_merge_unicode_lowercase_generations(self) -> None:
        """Resolve the exact configured display locator before trusting path_lower."""
        client = MockDropboxClient(
            token_supplier=lambda: None,
            config={'roots': [{'id': 'legacy', 'path': '/ꙋ'}]},
            instance_id='dbx',
        )
        client.seed_item('id:wrong', path='/Ꙋ', path_lower='/wrong', kind='folder')
        client.seed_item('id:root', path='/ꙋ', path_lower='/authoritative', kind='folder')
        client.seed_item(
            'id:file',
            path='/unrelated/file',
            path_lower='/authoritative/file',
            kind='file',
        )
        self.assertEqual(client.list_roots()['items'][0]['id'], 'id:root')
        self.assertEqual(client.get_metadata(root='legacy', item_ref='id:file')['item']['id'], 'id:file')

    def test_root_locator_matches_ascii_display_case(self) -> None:
        """Match configured and seeded display locators with Dropbox-like ASCII casing."""
        client = MockDropboxClient(
            token_supplier=lambda: None,
            config={'roots': [{'id': 'projects', 'path': '/projects'}]},
            instance_id='dbx',
        )
        client.seed_item('id:root', path='/Projects', kind='folder')
        client.seed_item('id:file', path='/Projects/file', kind='file')
        self.assertEqual(client.list_roots()['items'][0]['id'], 'id:root')
        self.assertEqual(client.get_metadata(root='projects', item_ref='id:file')['item']['id'], 'id:file')

    def test_file_root_metadata_and_folder_only_failures(self) -> None:
        """Support file root inspection while rejecting list and search."""
        client = self._client()
        self.assertEqual(client.get_metadata(root='single', item_ref='id:single')['item']['kind'], 'file')
        with self.assertRaises(DropboxConfigError):
            client.list_folder(root='single')
        with self.assertRaises(DropboxConfigError):
            client.search(root='single', query='single')

    def test_search_filters_kinds_paginates_and_rechecks_resume(self) -> None:
        """Search deterministic names with kind filters and current move checks."""
        client = self._client()
        client.seed_item('id:report-folder', path='/Projects/report folder', kind='folder')
        all_results = client.search(root='projects', query='report')
        self.assertEqual([item['id'] for item in all_results['items']], ['id:file', 'id:report-folder'])
        files = client.search(root='projects', query='report', kinds=('file',), max_results=1)
        self.assertEqual([item['id'] for item in files['items']], ['id:file'])
        folders = client.search(root='projects', query='report', kinds=('folder',))
        self.assertEqual([item['id'] for item in folders['items']], ['id:report-folder'])
        with self.assertRaises(DropboxConfigError):
            client.search(root='projects', query='report', kinds=('deleted',))

    def test_search_resume_does_not_skip_after_prior_ranked_item_moves(self) -> None:
        """Resume after the last ranked key while reauthorizing the current result set."""
        client = self._client()
        client.seed_item('id:second', path='/Projects/second report.txt', kind='file')
        first = client.search(root='projects', query='report', max_results=1)
        self.assertEqual([item['id'] for item in first['items']], ['id:file'])
        client.seed_item('id:file', path='/Outside/report.txt', kind='file')
        resumed = client.search(root='projects', query='report', cursor=first['next_cursor'], max_results=1)
        self.assertEqual([item['id'] for item in resumed['items']], ['id:second'])

    def test_search_resume_discards_moved_buffered_item(self) -> None:
        """Skip a buffered search candidate that moved outside before resume."""
        client = self._client()
        client.seed_item('id:second', path='/Projects/second report.txt', kind='file')
        first = client.search(root='projects', query='report', max_results=1)
        client.seed_item('id:second', path='/Outside/second report.txt', kind='file')

        resumed = client.search(root='projects', query='report', cursor=first['next_cursor'], max_results=1)

        self.assertEqual(resumed, {'items': [], 'next_cursor': None})

    def test_seeded_metadata_has_no_content_or_links(self) -> None:
        """Keep the mock surface metadata-only."""
        item = self._client().get_metadata(root='projects', item_ref='id:file')['item']
        self.assertNotIn('content', item)
        self.assertNotIn('shared', repr(item))
        self.assertIsNone(item['web_url'])
