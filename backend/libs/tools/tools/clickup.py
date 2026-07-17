# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""ClickUp tool: map LLM-visible functions to ClickUpClient methods.

Full surface exposed (including delete); per-instance allow/deny gates it (deny delete in
examples). `ClickUpError`s map to the same `{ok, error}` failure result as the Gmail tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from libs.clients.clickup.client import ClickUpClient
from libs.clients.clickup.errors import (
    ClickUpAuthError,
    ClickUpError,
    ClickUpNotFoundError,
)
from libs.clients.clickup.protocol import ClickUpClientProtocol
from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext, token_supplier_for

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

_TASK_ID_DESC = 'ClickUp task id (from `list_tasks`/queue item `ref.resource_id`).'
_LIST_ID_DESC = 'ClickUp list id to create/list tasks in.'


def _failure(exc: ClickUpError) -> dict[str, Any]:
    """Map a ClickUpError to a uniform tool failure result (same shape as Gmail)."""
    if isinstance(exc, ClickUpNotFoundError):
        kind = 'not_found'
    elif isinstance(exc, ClickUpAuthError):
        kind = 'auth'
    else:
        kind = 'api'
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


class ClickUpTool(Tool):
    name = 'clickup'
    credential_type = 'clickup'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke closed over a ClickUpClient.

        Credentials come from ``ctx.secret_supplier_factory``; per-instance
        config and client factory overrides from ``instance`` / ``ctx``.
        """
        cfg = instance.config if instance else {}
        token_supplier = token_supplier_for(
            ctx,
            credential_type=self.credential_type,
            credential_ref=instance.credential_ref if instance else None,
        )
        client_factory = ctx.client_factories.get(self.name)
        factory: Callable[..., ClickUpClientProtocol] = client_factory or ClickUpClient
        client = factory(token_supplier=token_supplier, config=cfg)
        team_id = cfg.get('team_id')

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            try:
                return self._dispatch(client, team_id, function, arguments)
            except ClickUpError as exc:
                return _failure(exc)

        return invoke

    def _dispatch(
        self, client: ClickUpClientProtocol, team_id: str | None, function: str, arguments: dict[str, Any]
    ) -> Any:
        """Route one function call to the matching client method."""
        if function == 'list_spaces':
            tid = arguments.get('team_id') or team_id
            if not tid:
                raise ValueError('team_id is required')
            return client.list_spaces(tid)
        if function == 'list_lists':
            return client.list_lists(arguments['space_id'])
        if function == 'list_tasks':
            return client.list_tasks(
                list_id=arguments['list_id'],
                statuses=tuple(arguments.get('statuses', [])),
            )
        if function == 'get_task':
            return client.get_task(arguments['task_id'])
        if function == 'create_task':
            return client.create_task(
                list_id=arguments['list_id'],
                name=arguments['name'],
                description=arguments.get('description'),
                status=arguments.get('status'),
            )
        if function == 'update_task':
            task_id = arguments['task_id']
            fields = {key: value for key, value in arguments.items() if key != 'task_id'}
            return client.update_task(task_id, **fields)
        if function == 'create_comment':
            return client.create_comment(arguments['task_id'], text=arguments['text'])
        if function == 'delete_task':
            return {'ok': True, **client.delete_task(arguments['task_id'])}
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """LLM-visible ClickUp functions (handlers require ``bind``)."""
        task_only = {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'description': _TASK_ID_DESC}},
            'required': ['task_id'],
        }
        return [
            ToolFunction(
                'list_spaces',
                'List spaces in a workspace.',
                {
                    'type': 'object',
                    'properties': {
                        'team_id': {'type': 'string', 'description': 'Workspace id (defaults to config.team_id).'},
                    },
                    'required': [],
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'list_lists',
                'List lists in a space.',
                {
                    'type': 'object',
                    'properties': {'space_id': {'type': 'string'}},
                    'required': ['space_id'],
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'list_tasks',
                'List tasks in a list.',
                {
                    'type': 'object',
                    'properties': {
                        'list_id': {'type': 'string', 'description': _LIST_ID_DESC},
                        'statuses': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['list_id'],
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction('get_task', 'Fetch one task.', task_only, self._unbound, readonly=True),
            ToolFunction(
                'create_task',
                'Create a task in a list (INBOX routing).',
                {
                    'type': 'object',
                    'properties': {
                        'list_id': {'type': 'string', 'description': _LIST_ID_DESC},
                        'name': {'type': 'string'},
                        'description': {'type': 'string'},
                        'status': {'type': 'string'},
                    },
                    'required': ['list_id', 'name'],
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction(
                'update_task',
                'Update task fields.',
                {
                    'type': 'object',
                    'properties': {
                        'task_id': {'type': 'string', 'description': _TASK_ID_DESC},
                        'name': {'type': 'string'},
                        'status': {'type': 'string'},
                        'description': {'type': 'string'},
                    },
                    'required': ['task_id'],
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction(
                'create_comment',
                'Add a comment to a task.',
                {
                    'type': 'object',
                    'properties': {
                        'task_id': {'type': 'string', 'description': _TASK_ID_DESC},
                        'text': {'type': 'string'},
                    },
                    'required': ['task_id', 'text'],
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction(
                'delete_task',
                'Delete a task (deny by default).',
                task_only,
                self._unbound,
                readonly=False,
            ),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('clickup tool requires bind(token_supplier=..., config=...)')
