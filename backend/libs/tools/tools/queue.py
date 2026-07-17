# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Queue tool for agent-scoped list/put/take/complete/fail operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

_QUEUE_ID_DESC = 'Queue id from this agent\'s config (``queues[].id``). Lowercase slug, max 64 characters.'
_EXTERNAL_ID_DESC = (
    'Optional deduplication key when enqueueing from a source adapter; max 255 characters. '
    'Omit for items enqueued directly in-session.'
)
_ITEM_ID_DESC = 'UUID string returned by ``take`` for the item this session currently holds.'


def _payload_description() -> str:
    """Describe payload size limits using the queue command constant."""
    from apps.queues.services.commands import MAX_PAYLOAD_BYTES

    return f'JSON object stored on the queue item. UTF-8 JSON encoding must be at most ' f'{MAX_PAYLOAD_BYTES} bytes.'


class QueueTool(Tool):
    name = 'queue'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke callable closed over session and agent context."""
        agent_id = ctx.agent_id
        session_id = ctx.session_id

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function == 'list':
                return self._list(agent_id=agent_id)
            if function == 'put':
                return self._put(
                    agent_id=agent_id,
                    queue=arguments['queue'],
                    payload=arguments['payload'],
                    external_id=arguments.get('external_id'),
                )
            if function == 'take':
                return self._take(
                    agent_id=agent_id,
                    session_id=session_id,
                    queue=arguments['queue'],
                )
            if function == 'complete':
                return self._complete(
                    session_id=session_id,
                    item_id=arguments['item_id'],
                )
            if function == 'fail':
                return self._fail(
                    session_id=session_id,
                    item_id=arguments['item_id'],
                    reason=arguments.get('reason', ''),
                )
            raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')

        return invoke

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """LLM-visible queue tool definitions (handlers require ``bind``)."""
        return [
            ToolFunction(
                name='list',
                description='List queue ids configured on this agent (from ``queues[]`` in the agent spec).',
                parameters={'type': 'object', 'properties': {}, 'required': []},
                handler=self._list_unbound,
                readonly=True,
            ),
            ToolFunction(
                name='put',
                description='Enqueue a payload on one of this agent\'s own queues.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'queue': {'type': 'string', 'description': _QUEUE_ID_DESC},
                        'payload': {'type': 'object', 'description': _payload_description()},
                        'external_id': {'type': 'string', 'description': _EXTERNAL_ID_DESC},
                    },
                    'required': ['queue', 'payload'],
                },
                handler=self._put_unbound,
                readonly=False,
            ),
            ToolFunction(
                name='take',
                description='Claim the next available item from a queue on this agent.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'queue': {'type': 'string', 'description': _QUEUE_ID_DESC},
                    },
                    'required': ['queue'],
                },
                handler=self._take_unbound,
                readonly=True,
            ),
            ToolFunction(
                name='complete',
                description='Mark a taken queue item as completed.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'item_id': {'type': 'string', 'description': _ITEM_ID_DESC},
                    },
                    'required': ['item_id'],
                },
                handler=self._complete_unbound,
                readonly=False,
            ),
            ToolFunction(
                name='fail',
                description='Mark a taken queue item as failed.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'item_id': {'type': 'string', 'description': _ITEM_ID_DESC},
                        'reason': {
                            'type': 'string',
                            'description': 'Optional human-readable failure reason stored on the attempt.',
                        },
                    },
                    'required': ['item_id'],
                },
                handler=self._fail_unbound,
                readonly=False,
            ),
        ]

    @staticmethod
    def _list_unbound(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('queue.list requires session binding')

    @staticmethod
    def _put_unbound(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('queue.put requires session binding')

    @staticmethod
    def _take_unbound(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('queue.take requires session binding')

    @staticmethod
    def _complete_unbound(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('queue.complete requires session binding')

    @staticmethod
    def _fail_unbound(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('queue.fail requires session binding')

    @staticmethod
    def _list(*, agent_id: UUID | None) -> dict[str, Any]:
        """Return queue ids materialized for the session agent."""
        from apps.agents.models import Agent
        from apps.queues.services import queries

        if agent_id is None:
            raise ValueError('session context required')
        agent = Agent.objects.get(pk=agent_id)
        queues = queries.list_queues(agent=agent)
        return {'queues': [q.queue_id for q in queues]}

    @staticmethod
    def _put(
        *,
        agent_id: UUID | None,
        queue: str,
        payload: dict[str, Any],
        external_id: str | None = None,
    ) -> dict[str, Any]:
        """Enqueue on a queue owned by the session agent."""
        from apps.agents.models import Agent
        from apps.queues.services import commands, queries

        if agent_id is None:
            raise ValueError('session context required')
        agent = Agent.objects.get(pk=agent_id)
        target_queue = queries.get_queue(agent=agent, queue_id=queue)
        if target_queue is None:
            raise ValueError(f'unknown queue {queue!r}')
        result = commands.put_item(
            queue=target_queue,
            payload=payload,
            external_id=external_id,
        )
        return {'item_id': str(result.item_id), 'created': result.created}

    @staticmethod
    def _take(
        *,
        agent_id: UUID | None,
        session_id: UUID | None,
        queue: str,
    ) -> dict[str, Any]:
        """Claim the next item from a queue on the session's agent."""
        from apps.agents.models import Agent
        from apps.queues.services import commands, queries

        if agent_id is None or session_id is None:
            raise ValueError('session context required')
        agent = Agent.objects.get(pk=agent_id)
        target_queue = queries.get_queue(agent=agent, queue_id=queue)
        if target_queue is None:
            raise ValueError(f'unknown queue {queue!r}')
        result = commands.take_item(queue=target_queue, session_id=session_id)
        if result is None:
            return {'item': None}
        return {
            'item_id': str(result.item_id),
            'payload': result.payload,
            'attempt': result.attempt_count,
        }

    @staticmethod
    def _complete(
        *,
        session_id: UUID | None,
        item_id: str,
    ) -> dict[str, Any]:
        """Mark a taken item complete; caller must be the taker session."""
        from apps.queues.services import commands

        if session_id is None:
            raise ValueError('session context required')
        commands.complete_item(item_id=UUID(item_id), session_id=session_id)
        return {'ok': True}

    @staticmethod
    def _fail(
        *,
        session_id: UUID | None,
        item_id: str,
        reason: str = '',
    ) -> dict[str, Any]:
        """Mark a taken item failed; caller must be the taker session."""
        from apps.queues.services import commands

        if session_id is None:
            raise ValueError('session context required')
        commands.fail_item(item_id=UUID(item_id), session_id=session_id, reason=reason)
        return {'ok': True}
