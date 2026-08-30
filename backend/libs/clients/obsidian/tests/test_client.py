# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for ObsidianVaultClient using an injected httpx MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
from libs.clients.obsidian.client import ObsidianVaultClient
from libs.clients.obsidian.errors import (
    ObsidianAuthError,
    ObsidianConfigError,
    ObsidianForbiddenError,
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
    ObsidianUnavailableError,
)

from olib.py.django.test.cases import OTestCase


def _client(handler: Callable[[httpx.Request], httpx.Response], *, agent_id: str = 'agent-1') -> ObsidianVaultClient:
    """Build an ObsidianVaultClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    return ObsidianVaultClient(
        base_url='http://vault.internal',
        token='service-token',
        agent_id=agent_id,
        transport=transport,
    )


def _error_response(status: int, *, kind: str, message: str = 'failed') -> httpx.Response:
    """Build the vault service's normative `{"ok": false, "error": {...}}` failure body."""
    return httpx.Response(status, json={'ok': False, 'error': {'kind': kind, 'message': message}})


class TestObsidianVaultClientConstruction(OTestCase):
    def test_rejects_blank_constructor_fields(self) -> None:
        cases: tuple[tuple[str, str, str], ...] = (
            ('', 't', 'a'),
            ('http://x', '', 'a'),
            ('http://x', 't', ''),
            ('http://x', 't', '   '),
        )
        for base_url, token, agent_id in cases:
            with (
                self.subTest(base_url=base_url, token=token, agent_id=agent_id),
                self.assertRaises(ObsidianConfigError),
            ):
                ObsidianVaultClient(base_url=base_url, token=token, agent_id=agent_id)


class TestObsidianVaultClientLifecycle(OTestCase):
    def test_ensure_vaults_puts_bindings_with_auth_header(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            captured['auth'] = request.headers.get('Authorization')
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'ok': True, 'vaults': [{'vault_id': 'Personal', 'ready': False}]})

        bindings = [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}]
        _client(handler, agent_id='agent-42').ensure_vaults(bindings)

        self.assertEqual(captured['method'], 'PUT')
        self.assertEqual(captured['path'], '/v1/agents/agent-42/vaults')
        self.assertEqual(captured['auth'], 'Bearer service-token')
        self.assertEqual(captured['body'], {'bindings': bindings})

    def test_release_vaults_issues_delete(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            return httpx.Response(200, json={'ok': True, 'released': ['Personal']})

        _client(handler, agent_id='agent-42').release_vaults()

        self.assertEqual(captured['method'], 'DELETE')
        self.assertEqual(captured['path'], '/v1/agents/agent-42/vaults')


class TestObsidianVaultClientStatus(OTestCase):
    def test_get_status_gets_vault_path_and_returns_bool_fields(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            captured['params'] = dict(request.url.params)
            captured['auth'] = request.headers.get('Authorization')
            captured['timeout'] = request.extensions['timeout']
            return httpx.Response(
                200,
                json={
                    'vault_id': 'Personal',
                    'ready': True,
                    'initial_sync_complete': True,
                    'sync_process_alive': False,
                },
            )

        body = _client(handler).get_status(vault_id='Personal')

        self.assertEqual(captured['method'], 'GET')
        self.assertEqual(captured['path'], '/v1/vaults/Personal/status')
        self.assertEqual(captured['params'], {})
        self.assertEqual(captured['auth'], 'Bearer service-token')
        self.assertEqual(captured['timeout'], {'connect': 2.0, 'read': 2.0, 'write': 2.0, 'pool': 2.0})
        self.assertEqual(
            body,
            {
                'vault_id': 'Personal',
                'ready': True,
                'initial_sync_complete': True,
                'sync_process_alive': False,
            },
        )

    def test_get_status_rejects_non_bool_flags(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    'vault_id': 'Personal',
                    'ready': 1,
                    'initial_sync_complete': True,
                    'sync_process_alive': True,
                },
            )

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).get_status(vault_id='Personal')

    def test_get_status_rejects_blank_vault_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    'vault_id': '',
                    'ready': True,
                    'initial_sync_complete': True,
                    'sync_process_alive': True,
                },
            )

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).get_status(vault_id='Personal')

    def test_get_status_rejects_missing_flags(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'vault_id': 'Personal', 'ready': True})

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).get_status(vault_id='Personal')

    def test_get_status_maps_bare_401_to_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'detail': 'Unauthorized'})

        with self.assertRaises(ObsidianAuthError):
            _client(handler).get_status(vault_id='Personal')


class TestObsidianVaultClientFileOps(OTestCase):
    def test_list_dir_sends_query_params_and_returns_entries(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['path'] = request.url.path
            captured['params'] = dict(request.url.params)
            return httpx.Response(200, json={'ok': True, 'entries': ['a.md', 'b.md']})

        entries = _client(handler).list_dir(vault_id='Personal', path='Journal')

        self.assertEqual(entries, ['a.md', 'b.md'])
        self.assertEqual(captured['path'], '/v1/agents/agent-1/files')
        self.assertEqual(captured['params'], {'vault_id': 'Personal', 'path': 'Journal'})

    def test_read_text_returns_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, '/v1/agents/agent-1/files/content')
            return httpx.Response(200, json={'ok': True, 'content': 'hello'})

        self.assertEqual(_client(handler).read_text(vault_id='Personal', path='Journal/a.md'), 'hello')

    def test_write_text_puts_json_content_body(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['params'] = dict(request.url.params)
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'ok': True})

        _client(handler).write_text(vault_id='Personal', path='Journal/a.md', content='hello')

        self.assertEqual(captured['method'], 'PUT')
        self.assertEqual(captured['params'], {'vault_id': 'Personal', 'path': 'Journal/a.md'})
        self.assertEqual(captured['body'], {'content': 'hello'})

    def test_append_text_posts_json_content_body(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'ok': True})

        _client(handler).append_text(vault_id='Personal', path='Journal/a.md', content='more')

        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['path'], '/v1/agents/agent-1/files/append')
        self.assertEqual(captured['body'], {'content': 'more'})

    def test_list_dir_rejects_non_string_entries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'ok': True, 'entries': ['a.md', 1]})

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).list_dir(vault_id='Personal', path='Journal')

    def test_read_text_rejects_non_string_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'ok': True, 'content': None})

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')


class TestObsidianVaultClientErrorMapping(OTestCase):
    def test_maps_each_error_kind_to_its_typed_failure(self) -> None:
        cases = (
            ('sync_pending', ObsidianSyncPendingError),
            ('outside_root', ObsidianOutsideRootError),
            ('not_found', ObsidianNotFoundError),
            ('forbidden', ObsidianForbiddenError),
            ('auth', ObsidianAuthError),
            ('config', ObsidianConfigError),
            ('unavailable', ObsidianUnavailableError),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):

                def handler(request: httpx.Request, kind: str = kind) -> httpx.Response:
                    return _error_response(503, kind=kind, message=f'{kind} failed')

                with self.assertRaisesRegex(expected, f'{kind} failed'):
                    _client(handler).read_text(vault_id='Personal', path='Journal/a.md')

    def test_bare_401_without_error_body_maps_to_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'detail': 'Unauthorized'})

        with self.assertRaises(ObsidianAuthError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')

    def test_unrecognized_status_without_kind_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={'detail': 'boom'})

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')

    def test_non_json_failure_body_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text='upstream is down')

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')

    def test_success_status_with_non_json_body_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text='not json')

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')

    def test_transport_failure_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError('connection refused')

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).read_text(vault_id='Personal', path='Journal/a.md')
