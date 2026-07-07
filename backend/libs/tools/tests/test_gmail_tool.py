# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for GmailTool (client stubbed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from libs.clients.gmail import GmailClient
from libs.clients.gmail.errors import GmailNotFoundError
from libs.tools.tools.gmail import GmailTool

from olib.py.django.test.cases import OTestCase


class _FakeGmailClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def list_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('list_messages', (), kwargs))
        return {'message_ids': ['m1'], 'next_page_token': None}

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        self.calls.append(('get_message', (message_id,), {'fmt': fmt}))
        if message_id == 'missing':
            raise GmailNotFoundError('no such message')
        return {'id': message_id, 'snippet': 'hi'}

    def modify_labels(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('modify_labels', (message_id,), kwargs))
        return {'id': message_id}

    def ensure_label_ids(self, names: tuple[str, ...]) -> list[str]:
        self.calls.append(('ensure_label_ids', (names,), {}))
        return ['L-new']

    def archive(self, message_id: str) -> dict[str, Any]:
        self.calls.append(('archive', (message_id,), {}))
        return {'id': message_id}


class TestGmailTool(OTestCase):
    def _bound(self, fake: _FakeGmailClient) -> Callable[[str, dict[str, Any]], Any]:
        tool = GmailTool()
        return tool.bind(
            token_supplier=lambda: '{"sa": true}',
            config={'subject': 'me@example.com'},
            client_factory=cast(Callable[..., GmailClient], lambda **kw: fake),
        )

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        fns = {f.name: f for f in GmailTool().functions()}
        self.assertEqual(
            set(fns),
            {'list', 'read', 'list_labels', 'get_attachment', 'label', 'archive', 'mark_spam', 'trash', 'send'},
        )
        self.assertTrue(fns['list'].readonly)
        self.assertTrue(fns['read'].readonly)
        self.assertFalse(fns['archive'].readonly)
        self.assertFalse(fns['send'].readonly)

    def test_list_maps_to_client(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out['message_ids'], ['m1'])
        self.assertEqual(fake.calls[0][0], 'list_messages')

    def test_archive_maps_to_client(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('archive', {'message_id': 'm1'})
        self.assertEqual(out, {'ok': True, 'id': 'm1'})
        self.assertEqual(fake.calls[0][0], 'archive')

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('read', {'message_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')

    def test_label_add_names_resolves_via_ensure_label_ids(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('label', {'message_id': 'm1', 'add_names': ['x-act']})
        self.assertEqual(out, {'ok': True, 'id': 'm1'})
        self.assertEqual(fake.calls[0][0], 'ensure_label_ids')
