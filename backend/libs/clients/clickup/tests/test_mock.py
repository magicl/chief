# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the in-memory ClickUp test client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.clients.clickup.mock import MockClickUpClient
from libs.clients.clickup.protocol import ClickUpClientProtocol
from libs.tools.tools.clickup import ClickUpTool

from olib.py.django.test.cases import OTestCase


def _invoke_with(client: MockClickUpClient, *, team_id: str | None = None) -> Callable[[str, dict[str, Any]], Any]:
    """Bind ClickUpTool to a supplied mock client."""
    config = {'team_id': team_id} if team_id is not None else {}
    return ClickUpTool().bind(token_supplier=lambda: None, config=config, client_factory=lambda **_kwargs: client)


class TestMockClickUpClient(OTestCase):
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

        self.assertEqual(spaces, {'spaces': [{'id': 'sp1', 'name': 'Ops'}]})
        self.assertEqual(lists, {'lists': [{'id': 'list1', 'name': 'Inbox'}]})
        self.assertEqual([task['id'] for task in tasks['tasks']], ['task1'])

    def test_create_task_records_payload_and_returns_synthetic_id(self) -> None:
        client = MockClickUpClient(token_supplier=lambda: None, config={})

        result = _invoke_with(client)(
            'create_task',
            {'list_id': 'list1', 'name': 'New task', 'description': 'Details', 'status': 'open'},
        )

        self.assertEqual(result, {'id': 'mock-task-1'})
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
        self.assertEqual(comment, {'id': 'mock-comment-1'})
        self.assertEqual(client.comments, [{'id': 'mock-comment-1', 'task_id': 'task1', 'text': 'Looks good'}])
        self.assertEqual(deleted, {'ok': True, 'id': 'task1', 'deleted': True})
        self.assertEqual(client.deleted_tasks, ['task1'])
