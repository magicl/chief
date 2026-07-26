# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the in-memory ClickUp test client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.clickup.errors import ClickUpNotFoundError
from libs.clients.clickup.mock import MockClickUpClient
from libs.clients.clickup.protocol import ClickUpClientProtocol
from libs.tools.context import ToolContext
from libs.tools.tools.clickup import ClickUpTool

from olib.py.django.test.cases import OTestCase


def _invoke_with(client: MockClickUpClient, *, team_id: str | None = None) -> Callable[[str, dict[str, Any]], Any]:
    """Bind ClickUpTool to a supplied mock client."""
    config = {'team_id': team_id} if team_id is not None else {}
    inst = ToolInstance(id='clickup', type='clickup', config=config)
    ctx = ToolContext(
        spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
        user_id=1,
        client_factories={'clickup': lambda **_kwargs: client},
    )
    return ClickUpTool().bind(ctx, inst)


class TestMockClickUpClient(OTestCase):
    def test_get_task_expansions_match_protocol_and_return_copies(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        protocol_client: ClickUpClientProtocol = client
        client.seed_task(
            'list1',
            {
                'id': 'task1',
                'name': 'Parent',
                'markdown_description': '**Details**',
            },
        )
        client.seed_task('list1', {'id': 'sub1', 'name': 'Child', 'parent': 'task1'})

        default = protocol_client.get_task('task1')
        expanded = protocol_client.get_task(
            'task1',
            include_subtasks=True,
            include_markdown_description=True,
        )
        expanded['subtasks'][0]['name'] = 'Changed'

        self.assertEqual(default, {'id': 'task1', 'name': 'Parent'})
        self.assertEqual(
            client.get_task('task1', include_subtasks=True, include_markdown_description=True),
            {
                'id': 'task1',
                'name': 'Parent',
                'subtasks': [{'id': 'sub1', 'name': 'Child', 'parent': 'task1'}],
                'markdown_description': '**Details**',
            },
        )

    def test_get_task_false_expansions_do_not_invent_sections(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        client.seed_task('list1', {'id': 'task1', 'name': 'Plain'})

        task = client.get_task('task1', include_subtasks=False, include_markdown_description=False)

        self.assertNotIn('subtasks', task)
        self.assertNotIn('markdown_description', task)

    def test_list_comments_returns_newest_first_copies_and_matches_protocol(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        protocol_client: ClickUpClientProtocol = client
        client.seed_task('list1', {'id': 'task1', 'name': 'Task'})
        client.seed_comment('task1', {'id': 'new', 'date': '200', 'comment_text': 'New'})
        client.seed_comment('task1', {'id': 'old', 'date': '100', 'comment_text': 'Old'})

        first = protocol_client.list_comments('task1')
        first['comments'][0]['comment_text'] = 'Changed'
        second = protocol_client.list_comments('task1')

        self.assertEqual([comment['id'] for comment in second['comments']], ['new', 'old'])
        self.assertEqual(second['comments'][0]['comment_text'], 'New')

    def test_list_comments_mixes_created_and_dated_seed_comments_newest_first(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        client.seed_task('list1', {'id': 'task1', 'name': 'Task'})
        client.seed_comment('task1', {'id': 'new-seed', 'date': '200', 'comment_text': 'New seed'})
        client.seed_comment('task1', {'id': 'old-seed', 'date': '100', 'comment_text': 'Old seed'})

        created = client.create_comment('task1', text='Created last')

        self.assertEqual(
            client.list_comments('task1'),
            {
                'comments': [
                    {'id': created['id'], 'date': '201', 'comment_text': 'Created last'},
                    {'id': 'new-seed', 'date': '200', 'comment_text': 'New seed'},
                    {'id': 'old-seed', 'date': '100', 'comment_text': 'Old seed'},
                ]
            },
        )
        self.assertEqual(
            client.comments,
            [{'id': created['id'], 'task_id': 'task1', 'text': 'Created last'}],
        )

    def test_delete_then_reseed_task_does_not_restore_stale_comments(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        client.seed_task('list1', {'id': 'task1', 'name': 'Old task'})
        client.seed_comment('task1', {'id': 'old-comment', 'date': '100', 'comment_text': 'Old'})

        client.delete_task('task1')
        client.seed_task('list1', {'id': 'task1', 'name': 'New task'})

        self.assertEqual(client.list_comments('task1'), {'comments': []})

    def test_list_comments_for_missing_task_raises_not_found(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})

        with self.assertRaisesRegex(ClickUpNotFoundError, 'clickup task not found: missing'):
            client.list_comments('missing')

    def test_seeded_spaces_lists_and_tasks_can_be_listed_by_tool(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={'team_id': 'team1'})
        protocol_client: ClickUpClientProtocol = client
        assert protocol_client is client
        client.seed_space('team1', {'id': 'sp1', 'name': 'Ops'})
        client.seed_list('sp1', {'id': 'list1', 'name': 'Inbox'})
        client.seed_task('list1', {'id': 'task1', 'name': 'Do thing', 'status': {'status': 'open'}})
        client.seed_task('list1', {'id': 'task2', 'name': 'Done thing', 'status': {'status': 'closed'}})
        invoke = _invoke_with(client, team_id='team1')

        spaces = invoke('list_spaces', {})
        lists = invoke('list_lists', {'space_id': 'sp1'})
        tasks = invoke('list_tasks', {'list_id': 'list1', 'statuses': ['open']})

        self.assertEqual(spaces, {'spaces': [{'id': 'sp1', 'name': 'Ops', 'archived': False}]})
        self.assertEqual(lists, {'lists': [{'id': 'list1', 'name': 'Inbox', 'archived': False}]})
        self.assertEqual([task['id'] for task in tasks['tasks']], ['task1'])

    def test_create_task_records_payload_and_returns_synthetic_id(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})

        result = _invoke_with(client)(
            'create_task',
            {'list_id': 'list1', 'name': 'New task', 'description': 'Details', 'status': 'open'},
        )

        self.assertEqual(result, {'ok': True, 'task_id': 'mock-task-1'})
        self.assertEqual(
            client.created_tasks,
            [{'id': 'mock-task-1', 'list_id': 'list1', 'name': 'New task', 'description': 'Details', 'status': 'open'}],
        )
        self.assertEqual(client.get_task('mock-task-1')['name'], 'New task')

    def test_update_comment_and_delete_mutations_change_seeded_task(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})
        client.seed_task('list1', {'id': 'task1', 'name': 'Original'})
        invoke = _invoke_with(client)

        updated = invoke('update_task', {'task_id': 'task1', 'name': 'Renamed'})
        comment = invoke('create_comment', {'task_id': 'task1', 'text': 'Looks good'})
        deleted = invoke('delete_task', {'task_id': 'task1'})

        self.assertEqual(updated['name'], 'Renamed')
        self.assertEqual(comment, {'ok': True, 'task_id': 'task1'})
        self.assertEqual(client.comments, [{'id': 'mock-comment-1', 'task_id': 'task1', 'text': 'Looks good'}])
        self.assertEqual(deleted, {'ok': True, 'task_id': 'task1', 'deleted': True})
        self.assertEqual(client.deleted_tasks, ['task1'])
