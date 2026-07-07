# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for ClickUpTool (client stubbed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from libs.clients.clickup import ClickUpClient
from libs.clients.clickup.errors import ClickUpNotFoundError
from libs.tools.tools.clickup import ClickUpTool

from olib.py.django.test.cases import OTestCase


class _FakeClickUpClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('list_tasks', kwargs))
        return {'tasks': [{'id': 't1'}], 'last_page': True}

    def get_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append(('get_task', {'task_id': task_id}))
        if task_id == 'missing':
            raise ClickUpNotFoundError('no such task')
        return {'id': task_id, 'name': 'T'}

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('create_task', kwargs))
        return {'id': 't9', 'name': kwargs['name']}


class TestClickUpTool(OTestCase):
    def _bound(self, fake: _FakeClickUpClient) -> Callable[[str, dict[str, Any]], Any]:
        return ClickUpTool().bind(
            token_supplier=lambda: 'pk_test',
            config={'team_id': '9'},
            client_factory=cast(Callable[..., ClickUpClient], lambda **kw: fake),
        )

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        fns = {f.name: f for f in ClickUpTool().functions()}
        self.assertEqual(
            set(fns),
            {
                'list_spaces',
                'list_lists',
                'list_tasks',
                'get_task',
                'create_task',
                'update_task',
                'create_comment',
                'delete_task',
            },
        )
        self.assertTrue(fns['list_tasks'].readonly)
        self.assertTrue(fns['get_task'].readonly)
        self.assertFalse(fns['create_task'].readonly)
        self.assertFalse(fns['delete_task'].readonly)

    def test_create_task_maps_to_client(self) -> None:
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)
        out = invoke('create_task', {'list_id': '901', 'name': 'New'})
        self.assertEqual(out['id'], 't9')
        self.assertEqual(fake.calls[0][0], 'create_task')

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)
        out = invoke('get_task', {'task_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')

    def test_list_spaces_without_team_id_raises(self) -> None:
        fake = _FakeClickUpClient()
        invoke = ClickUpTool().bind(
            token_supplier=lambda: 'pk_test',
            config={},
            client_factory=cast(Callable[..., ClickUpClient], lambda **kw: fake),
        )
        with self.assertRaises(ValueError):
            invoke('list_spaces', {})
