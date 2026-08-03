# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only queue domain access."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from apps.agents.models import Agent
from apps.queues.models import (
    Queue,
    QueueItem,
    QueueItemAttempt,
    QueueItemStatus,
    Source,
)
from django.db.models import Count, Q, TextField
from django.db.models.functions import Cast, Left
from libs.web_tables import ListPage, TableQuery, TableSchema, clamp_page


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


PAYLOAD_SEARCH_TEXT_CAP = 2000
"""Character cap on the payload JSON text scanned by the ``q`` filter (bounded string form)."""

QUEUE_ITEMS_SORT_FIELDS: dict[str, str] = {
    'status': 'status',
    'created_at': 'created_at',
    'external_id': 'external_id',
    'source': 'source__source_id',
    'attempt_count': 'attempt_count',
    # Sorts by the taker session's id, not a display name — there is no stable
    # human-readable session label available at the query layer.
    'taken_by': 'taken_by_session_id',
    'taken_at': 'taken_at',
    'completed_at': 'completed_at',
}

QUEUE_ITEMS_TABLE_SCHEMA = TableSchema(
    sort_keys=frozenset(QUEUE_ITEMS_SORT_FIELDS),
    default_sort='created_at',
    default_dir='desc',
    page_size=50,
    filter_keys=frozenset({'status', 'source', 'q'}),
)


@dataclass(frozen=True, slots=True)
class QueueSummary:
    """One agent-owned queue plus its per-status item counts, for the agent detail Queues section."""

    queue: Queue
    counts: dict[str, int]
    total: int


def list_queue_summaries(*, agent: Agent) -> list[QueueSummary]:
    """List *agent*'s queues (ordered by slug) with per-status item counts.

    Uses one grouped aggregate query across every one of the agent's queues,
    rather than one COUNT per queue, so the Queues section costs a constant
    number of queries regardless of how many queues the agent has.
    """
    agent_queues = list_queues(agent=agent)
    counts_by_queue: dict[UUID, dict[str, int]] = {
        queue.id: dict.fromkeys(QueueItemStatus.values, 0) for queue in agent_queues
    }
    rows = QueueItem.objects.filter(queue__agent=agent).values('queue_id', 'status').annotate(count=Count('id'))
    for row in rows:
        counts_by_queue.setdefault(row['queue_id'], dict.fromkeys(QueueItemStatus.values, 0))
        counts_by_queue[row['queue_id']][row['status']] = row['count']
    return [
        QueueSummary(
            queue=queue,
            counts=counts_by_queue[queue.id],
            total=sum(counts_by_queue[queue.id].values()),
        )
        for queue in agent_queues
    ]


def list_source_ids(*, queue: Queue) -> list[str]:
    """Return *queue*'s source ids, ordered, for the items-page source filter options."""
    return list(Source.objects.filter(queue=queue).order_by('source_id').values_list('source_id', flat=True))


def list_queue_items_page(*, queue: Queue, query: TableQuery) -> ListPage[QueueItem]:
    """Return one filtered/sorted/paginated page of *queue*'s items, all statuses included.

    Assumes *query* was built via ``parse_table_query(params, QUEUE_ITEMS_TABLE_SCHEMA)``,
    so ``query.sort`` is already guaranteed to be a key of ``QUEUE_ITEMS_SORT_FIELDS``.
    """
    qs = QueueItem.objects.filter(queue=queue).select_related('source', 'taken_by_session')

    status_filter = query.filters.get('status', '')
    if status_filter in QueueItemStatus.values:
        qs = qs.filter(status=status_filter)

    source_filter = query.filters.get('source', '')
    if source_filter:
        qs = qs.filter(source__source_id=source_filter)

    q_filter = query.filters.get('q', '')
    if q_filter:
        qs = qs.annotate(
            payload_text=Left(Cast('payload', output_field=TextField()), PAYLOAD_SEARCH_TEXT_CAP),
        ).filter(
            Q(external_id__icontains=q_filter)
            | Q(failure_reason__icontains=q_filter)
            | Q(payload_text__icontains=q_filter),
        )

    order_field = QUEUE_ITEMS_SORT_FIELDS[query.sort]
    ordering = order_field if query.dir == 'asc' else f'-{order_field}'
    qs = qs.order_by(ordering, 'id')

    total = qs.count()
    total_pages = 1 if total <= 0 else -(-total // QUEUE_ITEMS_TABLE_SCHEMA.page_size)
    page = clamp_page(query.page, total_pages)
    start = (page - 1) * QUEUE_ITEMS_TABLE_SCHEMA.page_size
    end = start + QUEUE_ITEMS_TABLE_SCHEMA.page_size
    rows = list(qs[start:end])

    return ListPage(
        rows=rows,
        total=total,
        page=page,
        page_size=QUEUE_ITEMS_TABLE_SCHEMA.page_size,
        sort=query.sort,
        dir=query.dir,
        filters=query.filters,
    )
