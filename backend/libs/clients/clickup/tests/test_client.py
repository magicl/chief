# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for ClickUpClient using an injected httpx MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
from libs.clients.clickup.client import ClickUpClient
from libs.clients.clickup.errors import (
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpNotFoundError,
)

from olib.py.django.test.cases import OTestCase


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = 'pk_test',
    sleep_fn: Callable[[float], None] | None = None,
) -> ClickUpClient:
    """Build a ClickUpClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    return ClickUpClient(
        token_supplier=lambda: token,
        config={'team_id': '9'},
        transport=transport,
        sleep_fn=sleep_fn or (lambda _secs: None),
    )


class TestClickUpClient(OTestCase):
    def test_list_tasks_parses_tasks_and_sends_auth_header(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['auth'] = request.headers.get('Authorization', '')
            captured['url'] = str(request.url)
            return httpx.Response(200, json={'tasks': [{'id': 't1'}, {'id': 't2'}], 'last_page': True})

        client = _client(handler)
        out = client.list_tasks(list_id='901', statuses=('open',))
        self.assertEqual([t['id'] for t in out['tasks']], ['t1', 't2'])
        self.assertEqual(captured['auth'], 'pk_test')
        self.assertIn('/list/901/task', captured['url'])
        self.assertIn('statuses%5B%5D=open', captured['url'])

    def test_list_tasks_up_to_paginates(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get('page', '0'))
            calls.append(page)
            if page == 0:
                return httpx.Response(200, json={'tasks': [{'id': 't1'}], 'last_page': False})
            return httpx.Response(200, json={'tasks': [{'id': 't2'}], 'last_page': True})

        client = _client(handler)
        tasks = client.list_tasks_up_to(list_id='901', max_results=2)
        self.assertEqual([t['id'] for t in tasks], ['t1', 't2'])
        self.assertEqual(calls, [0, 1])

    def test_create_task_posts_body(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'id': 't9', 'name': 'New'})

        client = _client(handler)
        out = client.create_task(list_id='901', name='New', description='d')
        self.assertEqual(out['id'], 't9')
        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['body'], {'name': 'New', 'description': 'd'})

    def test_update_task_puts_fields(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'id': 't1', 'name': 'Renamed'})

        client = _client(handler)
        out = client.update_task('t1', name='Renamed')
        self.assertEqual(out['name'], 'Renamed')
        self.assertEqual(captured['method'], 'PUT')
        self.assertEqual(captured['body'], {'name': 'Renamed'})

    def test_create_comment_posts_text(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'id': 'c1'})

        client = _client(handler)
        client.create_comment('t1', text='note')
        self.assertEqual(captured['body'], {'comment_text': 'note'})

    def test_delete_task_issues_delete(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            return httpx.Response(200, json={})

        client = _client(handler)
        client.delete_task('t1')
        self.assertEqual(captured['method'], 'DELETE')
        self.assertIn('/task/t1', captured['path'])

    def test_list_spaces_requires_team_id(self) -> None:
        client = _client(lambda _req: httpx.Response(200, json={}))
        with self.assertRaises(ValueError):
            client.list_spaces('')

    def test_list_spaces_and_lists_hit_expected_paths(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={'spaces': [], 'lists': []})

        client = _client(handler)
        client.list_spaces('9')
        client.list_lists('sp1')
        self.assertIn('/team/9/space', paths[0])
        self.assertIn('/space/sp1/list', paths[1])

    def test_404_maps_to_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={'err': 'not found', 'ECODE': 'x'})

        client = _client(handler)
        with self.assertRaises(ClickUpNotFoundError):
            client.get_task('missing')

    def test_401_maps_to_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'err': 'token invalid'})

        client = _client(handler)
        with self.assertRaises(ClickUpAuthError):
            client.list_teams()

    def test_missing_token_raises_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        client = _client(handler, token=None)
        with self.assertRaises(ClickUpAuthError):
            client.list_teams()

    def test_429_retries_once_then_succeeds(self) -> None:
        sleeps: list[float] = []
        attempts = {'n': 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts['n'] += 1
            if attempts['n'] == 1:
                return httpx.Response(429, headers={'Retry-After': '0'}, json={'err': 'rate'})
            return httpx.Response(200, json={'teams': []})

        client = _client(handler, sleep_fn=sleeps.append)
        out = client.list_teams()
        self.assertEqual(out, {'teams': []})
        self.assertEqual(attempts['n'], 2)
        self.assertEqual(sleeps, [0.0])

    def test_500_after_retry_raises_api_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={'err': 'boom'})

        client = _client(handler)
        with self.assertRaises(ClickUpAPIError):
            client.list_teams()
