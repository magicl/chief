# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-memory ClickUp client for tool and workflow tests."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from libs.clients.clickup.errors import ClickUpNotFoundError


class MockClickUpClient:
    """Small in-memory ClickUpClient replacement with deterministic mutation records."""

    def __init__(self, *, token_supplier: Callable[[], str | None], config: dict[str, Any] | None = None) -> None:
        """Create a mock with the same constructor shape as the real ClickUp client."""
        self._token_supplier = token_supplier
        self._config = config or {}
        self._spaces_by_team: dict[str, list[dict[str, Any]]] = {}
        self._lists_by_space: dict[str, list[dict[str, Any]]] = {}
        self._tasks_by_list: dict[str, list[dict[str, Any]]] = {}
        self._task_list_ids: dict[str, str] = {}
        self._comments_by_task: dict[str, list[dict[str, Any]]] = {}
        self._next_task_seq = 1
        self._next_comment_seq = 1
        self.created_tasks: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.deleted_tasks: list[str] = []

    def seed_space(self, team_id: str, space: dict[str, Any]) -> dict[str, Any]:
        """Add a space record under a team id."""
        stored = deepcopy(space)
        self._spaces_by_team.setdefault(team_id, []).append(stored)
        return deepcopy(stored)

    def seed_list(self, space_id: str, list_record: dict[str, Any]) -> dict[str, Any]:
        """Add a list record under a space id."""
        stored = deepcopy(list_record)
        self._lists_by_space.setdefault(space_id, []).append(stored)
        self._tasks_by_list.setdefault(str(stored['id']), [])
        return deepcopy(stored)

    def seed_task(self, list_id: str, task: dict[str, Any]) -> dict[str, Any]:
        """Add a task record under a list id."""
        stored = deepcopy(task)
        self._tasks_by_list.setdefault(list_id, []).append(stored)
        task_id = str(stored['id'])
        self._task_list_ids[task_id] = list_id
        self._comments_by_task.setdefault(task_id, [])
        return deepcopy(stored)

    def seed_comment(self, task_id: str, comment: dict[str, Any]) -> dict[str, Any]:
        """Add a raw provider comment under an existing task id."""
        self._task_ref(task_id)
        stored = deepcopy(comment)
        self._comments_by_task.setdefault(task_id, []).append(stored)
        return deepcopy(stored)

    def list_spaces(self, team_id: str) -> dict[str, Any]:
        """List seeded spaces for one team id."""
        if not team_id:
            raise ValueError('team_id is required')
        return {'spaces': deepcopy(self._spaces_by_team.get(team_id, []))}

    def list_lists(self, space_id: str) -> dict[str, Any]:
        """List seeded lists for one space id."""
        return {'lists': deepcopy(self._lists_by_space.get(space_id, []))}

    def list_tasks(self, *, list_id: str, statuses: tuple[str, ...] = ()) -> dict[str, Any]:
        """List seeded tasks for one list id, optionally filtering by status name."""
        tasks = self._tasks_by_list.get(list_id, [])
        if statuses:
            wanted = set(statuses)
            tasks = [task for task in tasks if self._status_name(task) in wanted]
        return {'tasks': deepcopy(tasks), 'last_page': True}

    def get_task(
        self,
        task_id: str,
        *,
        include_subtasks: bool = False,
        include_markdown_description: bool = False,
    ) -> dict[str, Any]:
        """Fetch a task with only the requested seeded expansion fields."""
        task = deepcopy(self._task_ref(task_id))
        if include_subtasks and 'subtasks' not in task:
            # Separate child seeds mirror ClickUp list responses, where parent ids link tasks.
            list_id = self._task_list_ids[task_id]
            task['subtasks'] = deepcopy(
                [candidate for candidate in self._tasks_by_list[list_id] if str(candidate.get('parent')) == task_id]
            )
        elif not include_subtasks:
            task.pop('subtasks', None)
        if not include_markdown_description:
            task.pop('markdown_description', None)
        return task

    def list_comments(self, task_id: str) -> dict[str, Any]:
        """List seeded and created comments newest-first without exposing stored values."""
        self._task_ref(task_id)
        comments = self._comments_by_task.get(task_id, [])
        if comments and all(str(comment.get('date', '')).isdigit() for comment in comments):
            comments = sorted(comments, key=lambda comment: int(str(comment['date'])), reverse=True)
        else:
            comments = list(reversed(comments))
        return {'comments': deepcopy(comments)}

    def create_task(
        self, *, list_id: str, name: str, description: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """Create and record a task in a list, returning a synthetic id."""
        task_id = f'mock-task-{self._next_task_seq}'
        self._next_task_seq += 1
        task: dict[str, Any] = {'id': task_id, 'list_id': list_id, 'name': name}
        if description is not None:
            task['description'] = description
        if status is not None:
            task['status'] = status
        self.created_tasks.append(deepcopy(task))
        self.seed_task(list_id, task)
        return {'id': task_id}

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Update a stored task in place and return the updated task."""
        task = self._task_ref(task_id)
        task.update(fields)
        return deepcopy(task)

    def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
        """Record a comment against an existing task and return a synthetic id."""
        self._task_ref(task_id)
        comment_id = f'mock-comment-{self._next_comment_seq}'
        self._next_comment_seq += 1
        mutation = {'id': comment_id, 'task_id': task_id, 'text': text}
        self.comments.append(deepcopy(mutation))
        provider_comment = {
            'id': comment_id,
            'date': str(self._next_comment_date(task_id)),
            'comment_text': text,
        }
        self._comments_by_task.setdefault(task_id, []).append(provider_comment)
        return {'id': comment_id}

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Remove a task from its list and record the deleted task id."""
        self._task_ref(task_id)
        list_id = self._task_list_ids.pop(task_id)
        self._tasks_by_list[list_id] = [task for task in self._tasks_by_list[list_id] if str(task.get('id')) != task_id]
        self._comments_by_task.pop(task_id, None)
        self.deleted_tasks.append(task_id)
        return {'id': task_id, 'deleted': True}

    def _next_comment_date(self, task_id: str) -> int:
        """Return a deterministic timestamp newer than every dated comment on the task."""
        dated = [
            int(str(comment['date']))
            for comment in self._comments_by_task.get(task_id, [])
            if str(comment.get('date', '')).isdigit()
        ]
        return max(dated, default=0) + 1

    def _task_ref(self, task_id: str) -> dict[str, Any]:
        """Return the mutable stored task or raise a typed not-found failure."""
        list_id = self._task_list_ids.get(task_id)
        if list_id is None:
            raise ClickUpNotFoundError(f'clickup task not found: {task_id}')
        for task in self._tasks_by_list[list_id]:
            if str(task.get('id')) == task_id:
                return task
        raise ClickUpNotFoundError(f'clickup task not found: {task_id}')

    def _status_name(self, task: dict[str, Any]) -> str | None:
        """Return a comparable status name from ClickUp's string or object status shapes."""
        status = task.get('status')
        if isinstance(status, dict):
            value = status.get('status')
            return str(value) if value is not None else None
        if status is None:
            return None
        return str(status)
