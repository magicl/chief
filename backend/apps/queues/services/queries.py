# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only queue domain access."""

from __future__ import annotations

from uuid import UUID

from apps.agents.models import Agent
from apps.queues.models import Queue, QueueItem, QueueItemAttempt, QueueItemStatus


def get_queue(*, agent: Agent, queue_id: str) -> Queue | None:
    """Return the agent-scoped queue with slug *queue_id*, if it exists."""
    return Queue.objects.filter(agent=agent, queue_id=queue_id).first()


def list_queues(*, agent: Agent) -> list[Queue]:
    """List all queues owned by *agent*, ordered by slug."""
    return list(Queue.objects.filter(agent=agent).order_by('queue_id'))


def get_item(*, item_id: UUID) -> QueueItem | None:
    """Return a queue item by primary key."""
    return QueueItem.objects.filter(pk=item_id).first()


def list_queue_items(
    *,
    queue: Queue,
    status: QueueItemStatus | str | None = None,
    limit: int | None = None,
) -> list[QueueItem]:
    """List items on *queue*, optionally filtered by *status* and capped by *limit*."""
    qs = QueueItem.objects.filter(queue=queue).order_by('created_at', 'id')
    if status is not None:
        qs = qs.filter(status=status)
    if limit is not None:
        qs = qs[:limit]
    return list(qs)


def list_attempts_for_item(*, item_id: UUID) -> list[QueueItemAttempt]:
    """Return every session attempt for *item_id*, ordered by attempt number."""
    return list(
        QueueItemAttempt.objects.filter(item_id=item_id).order_by('attempt_number'),
    )
