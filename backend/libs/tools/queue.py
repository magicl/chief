# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Queue tool for agent-scoped put/take/complete/fail operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from libs.tools.base import Tool, ToolFunction


class QueueTool(Tool):
    name = 'queue'

    def bind(
        self,
        *,
        user_id: int | None,
        agent_id: UUID | None,
        session_id: UUID | None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke callable closed over session and agent context."""

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function == 'put':
                return self._put(
                    user_id=user_id,
                    owner_agent=arguments.get('owner_agent'),
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

    def functions(self) -> list[ToolFunction]:
        """LLM-visible queue tool definitions (handlers require ``bind``)."""
        return [
            ToolFunction(
                name='put',
                description='Enqueue a payload on a queue owned by an agent.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'owner_agent': {'type': 'string'},
                        'queue': {'type': 'string'},
                        'payload': {'type': 'object'},
                        'external_id': {'type': 'string'},
                    },
                    'required': ['owner_agent', 'queue', 'payload'],
                },
                handler=self._put_unbound,
            ),
            ToolFunction(
                name='take',
                description='Claim the next available item from a queue on the session agent.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'queue': {'type': 'string'},
                    },
                    'required': ['queue'],
                },
                handler=self._take_unbound,
            ),
            ToolFunction(
                name='complete',
                description='Mark a taken queue item as completed.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'item_id': {'type': 'string'},
                    },
                    'required': ['item_id'],
                },
                handler=self._complete_unbound,
            ),
            ToolFunction(
                name='fail',
                description='Mark a taken queue item as failed.',
                parameters={
                    'type': 'object',
                    'properties': {
                        'item_id': {'type': 'string'},
                        'reason': {'type': 'string'},
                    },
                    'required': ['item_id'],
                },
                handler=self._fail_unbound,
            ),
        ]

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
    def _put(
        *,
        user_id: int | None,
        owner_agent: str | None,
        queue: str,
        payload: dict[str, Any],
        external_id: str | None = None,
    ) -> dict[str, Any]:
        """Enqueue on another agent's queue (same user scope)."""
        from apps.agents.models import Agent
        from apps.queues.services import commands, queries

        if user_id is None:
            raise ValueError('user context required')
        if owner_agent is None:
            raise ValueError('owner_agent is required')
        try:
            agent = Agent.objects.get(user_id=user_id, identifier=owner_agent)
        except Agent.DoesNotExist as exc:
            raise ValueError(f'unknown agent {owner_agent!r}') from exc
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
