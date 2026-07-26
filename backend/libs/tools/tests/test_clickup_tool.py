# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for ClickUpTool (client stubbed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.clickup import ClickUpClient
from libs.clients.clickup.errors import ClickUpAPIError, ClickUpNotFoundError
from libs.tools.context import ToolContext
from libs.tools.tools.clickup import ClickUpTool

from olib.py.django.test.cases import OTestCase


def _make_ctx(**client_factories: Any) -> ToolContext:
    """Build a ToolContext for ClickUp tests."""
    spec = AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_')
    return ToolContext(spec=spec, user_id=1, client_factories=client_factories)


class _FakeClickUpClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_spaces(self, team_id: str) -> dict[str, Any]:
        """Return one noisy workspace space."""
        self.calls.append(('list_spaces', {'team_id': team_id}))
        return {'spaces': [{'id': 's1', 'name': 'Engineering', 'archived': False, 'private': True}]}

    def list_lists(self, space_id: str) -> dict[str, Any]:
        """Return one noisy list in a space."""
        self.calls.append(('list_lists', {'space_id': space_id}))
        return {
            'lists': [
                {
                    'id': 'l1',
                    'name': 'Inbox',
                    'archived': False,
                    'space': {'id': space_id, 'name': 'Private'},
                    'statuses': [{'private': True}],
                }
            ]
        }

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        """Return one noisy task page."""
        self.calls.append(('list_tasks', kwargs))
        return {'tasks': [{'id': 't1', 'name': 'T', 'text_content': 'private'}], 'last_page': True}

    def get_task(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        """Return one full task or a typed not-found failure."""
        self.calls.append(('get_task', {'task_id': task_id, **kwargs}))
        if task_id == 'missing':
            raise ClickUpNotFoundError('no such task')
        return {'id': task_id, 'name': 'T', 'text_content': 'Body', 'creator': {'id': 1, 'username': 'Ada'}}

    def list_comments(self, task_id: str) -> dict[str, Any]:
        """Return one comment for a task."""
        self.calls.append(('list_comments', {'task_id': task_id}))
        return {'comments': [{'id': 'c1', 'comment_text': 'Comment', 'date': '1'}]}

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        """Return a noisy created task."""
        self.calls.append(('create_task', kwargs))
        return {'id': 't9', 'name': kwargs['name'], 'creator': {'id': 1, 'username': 'Private'}}

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Return noisy resulting task state."""
        self.calls.append(('update_task', {'task_id': task_id, **fields}))
        return {'id': task_id, **fields, 'workspace': {'id': 'private'}}

    def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
        """Return a provider comment identity distinct from its task."""
        self.calls.append(('create_comment', {'task_id': task_id, 'text': text}))
        return {'id': 'comment-9', 'date': 'private'}

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Return ClickUp's empty successful delete body."""
        self.calls.append(('delete_task', {'task_id': task_id}))
        return {}


class TestClickUpTool(OTestCase):
    def _bound(self, fake: _FakeClickUpClient) -> Callable[[str, dict[str, Any]], Any]:
        inst = ToolInstance(id='clickup', type='clickup', config={'team_id': '9'})
        ctx = _make_ctx(clickup=cast(Callable[..., ClickUpClient], lambda **kw: fake))
        ctx = ToolContext(
            spec=ctx.spec,
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: 'pk_test',
            client_factories=ctx.client_factories,
        )
        return ClickUpTool().bind(ctx, inst)

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        ctx = _make_ctx()
        fns = {f.name: f for f in ClickUpTool().functions(ctx)}
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

    def test_read_success_paths_return_compact_projections(self) -> None:
        """Project spaces, lists, and task pages without leaking provider-only fields."""
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)

        self.assertEqual(
            invoke('list_spaces', {}),
            {'spaces': [{'id': 's1', 'name': 'Engineering', 'archived': False}]},
        )
        self.assertEqual(
            invoke('list_lists', {'space_id': 's1'}),
            {'lists': [{'id': 'l1', 'name': 'Inbox', 'archived': False, 'space_id': 's1'}]},
        )
        tasks = invoke('list_tasks', {'list_id': 'l1'})
        self.assertEqual(tasks['last_page'], True)
        self.assertEqual(tasks['tasks'][0]['id'], 't1')
        self.assertNotIn('text_content', repr(tasks))
        self.assertNotIn('private', repr(tasks))

    def test_get_task_requests_expansions_and_projects_comments(self) -> None:
        """Fetch expanded task data and comments before returning one bounded full projection."""
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)

        out = invoke('get_task', {'task_id': 't1'})

        self.assertEqual(
            fake.calls,
            [
                (
                    'get_task',
                    {'task_id': 't1', 'include_subtasks': True, 'include_markdown_description': True},
                ),
                ('list_comments', {'task_id': 't1'}),
            ],
        )
        self.assertEqual(out['id'], 't1')
        self.assertEqual(out['description'], 'Body')
        self.assertEqual(out['comments'][0]['id'], 'c1')
        self.assertNotIn('text_content', out)

    def test_optional_comment_failure_returns_task_with_fixed_advisory(self) -> None:
        """Keep a successful task when comments fail without exposing provider failure text."""

        class _CommentFailureClient(_FakeClickUpClient):
            def list_comments(self, task_id: str) -> dict[str, Any]:
                """Raise a typed optional-fetch failure containing private provider text."""
                del task_id
                raise ClickUpAPIError('private provider comment failure', status=503)

        out = self._bound(_CommentFailureClient())('get_task', {'task_id': 't1'})

        self.assertEqual(
            out['advisories'],
            [{'code': 'comments_unavailable', 'message': 'Comments could not be loaded.'}],
        )
        self.assertEqual(out['comments'], [])
        self.assertNotIn('private provider comment failure', repr(out))
        self.assertNotIn('503', repr(out))

    def test_mutations_return_exact_compact_acknowledgements(self) -> None:
        """Return only affected task identity and useful resulting state for every mutation."""
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)

        self.assertEqual(
            invoke('create_task', {'list_id': '901', 'name': 'New'}),
            {'ok': True, 'task_id': 't9', 'name': 'New'},
        )
        self.assertEqual(
            invoke('update_task', {'task_id': 't9', 'name': 'Renamed', 'status': 'done'}),
            {'ok': True, 'task_id': 't9', 'name': 'Renamed', 'status': 'done'},
        )
        self.assertEqual(
            invoke('create_comment', {'task_id': 't9', 'text': 'Hello'}),
            {'ok': True, 'task_id': 't9'},
        )
        self.assertEqual(
            invoke('delete_task', {'task_id': 't9'}),
            {'ok': True, 'task_id': 't9', 'deleted': True},
        )
        self.assertNotIn('Private', repr(fake.calls))

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)
        out = invoke('get_task', {'task_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')

    def test_list_spaces_without_team_id_raises(self) -> None:
        fake = _FakeClickUpClient()
        inst = ToolInstance(id='clickup', type='clickup', config={})
        ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: 'pk_test',
            client_factories={'clickup': cast(Callable[..., ClickUpClient], lambda **kw: fake)},
        )
        invoke = ClickUpTool().bind(ctx, inst)
        with self.assertRaises(ValueError):
            invoke('list_spaces', {})

    def test_projection_failure_maps_to_safe_local_failure(self) -> None:
        """Map malformed successful provider payloads without returning raw content."""

        class _MalformedTaskClient(_FakeClickUpClient):
            def get_task(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
                """Return a task without stable identity and with private provider content."""
                del task_id, kwargs
                return {'name': 'private raw task', 'creator': {'workspace': 'private provider payload'}}

        out = self._bound(_MalformedTaskClient())('get_task', {'task_id': 't1'})

        self.assertEqual(
            out,
            {'ok': False, 'error': {'kind': 'api', 'message': 'Invalid ClickUp response'}},
        )
        self.assertNotIn('private raw task', repr(out))
        self.assertNotIn('private provider payload', repr(out))

    def test_malformed_success_payloads_map_to_fixed_failure_across_dispatch_families(self) -> None:
        """Reject non-object list and mutation payloads without leaking provider-controlled text."""

        class _MalformedSuccessClient(_FakeClickUpClient):
            def list_spaces(self, team_id: str) -> dict[str, Any]:
                """Return a malformed spaces response."""
                del team_id
                return cast(dict[str, Any], 'private raw spaces')

            def list_lists(self, space_id: str) -> dict[str, Any]:
                """Return a malformed lists response."""
                del space_id
                return cast(dict[str, Any], 'private raw lists')

            def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
                """Return a malformed task-page response."""
                del kwargs
                return cast(dict[str, Any], 'private raw task page')

            def create_task(self, **kwargs: Any) -> dict[str, Any]:
                """Return a malformed create response."""
                del kwargs
                return cast(dict[str, Any], 'private raw create')

            def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
                """Return a malformed update response."""
                del task_id, fields
                return cast(dict[str, Any], 'private raw update')

            def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
                """Return a malformed comment response."""
                del task_id, text
                return cast(dict[str, Any], 'private raw comment')

            def delete_task(self, task_id: str) -> dict[str, Any]:
                """Return a malformed delete response."""
                del task_id
                return cast(dict[str, Any], 'private raw delete')

        invoke = self._bound(_MalformedSuccessClient())
        calls: tuple[tuple[str, dict[str, str]], ...] = (
            ('list_spaces', {}),
            ('list_lists', {'space_id': 's1'}),
            ('list_tasks', {'list_id': 'l1'}),
            ('create_task', {'list_id': 'l1', 'name': 'New'}),
            ('update_task', {'task_id': 't1', 'name': 'Renamed'}),
            ('create_comment', {'task_id': 't1', 'text': 'Hello'}),
            ('delete_task', {'task_id': 't1'}),
        )

        for function, arguments in calls:
            with self.subTest(function=function):
                out = invoke(function, arguments)
                self.assertEqual(
                    out,
                    {'ok': False, 'error': {'kind': 'api', 'message': 'Invalid ClickUp response'}},
                )
                self.assertNotIn('private raw', repr(out))
