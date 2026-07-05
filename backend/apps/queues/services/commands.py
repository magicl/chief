# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Queue write commands."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from apps.agents.models import Agent, AgentConfig
from apps.queues.exceptions import (
    QueueItemNotFoundError,
    QueueItemStateError,
    QueueNotTakerError,
    QueuePayloadTooLargeError,
    QueueValidationError,
)
from apps.queues.models import (
    Queue,
    QueueItem,
    QueueItemAttempt,
    QueueItemAttemptOutcome,
    QueueItemStatus,
    Source,
    SourceStatus,
)
from apps.queues.releasable import is_session_releasable
from apps.sessions.models import AgentSession
from django.db import IntegrityError, transaction
from django.utils import timezone
from libs.agent_spec import QueueSpec, SourceSpec

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 65536


def _notify_queue_item_available(queue_id: UUID) -> None:
    """Enqueue per-queue trigger dispatch via Celery (no runner import at load time)."""
    from celery import current_app

    current_app.send_task(
        'apps.runner.trigger_tasks.dispatch_queue_triggers_for_queue',
        args=[str(queue_id)],
    )


def _schedule_queue_dispatch_on_commit(queue_id: UUID) -> None:
    """Fire queue trigger dispatch after the enclosing transaction commits."""
    transaction.on_commit(lambda: _notify_queue_item_available(queue_id))


@dataclass(frozen=True, slots=True)
class PutResult:
    item_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class TakeResult:
    item_id: UUID
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ReleaseStats:
    released: int = 0
    exhausted: int = 0


def _validate_payload_size(payload: dict[str, Any]) -> None:
    """Reject payloads whose JSON encoding exceeds ``MAX_PAYLOAD_BYTES``."""
    encoded = json.dumps(payload).encode('utf-8')
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise QueuePayloadTooLargeError(
            f'payload exceeds maximum size of {MAX_PAYLOAD_BYTES} bytes',
        )


@transaction.atomic
def put_item(
    *,
    queue: Queue,
    payload: dict[str, Any],
    source: Source | None = None,
    external_id: str | None = None,
) -> PutResult:
    """Enqueue *payload* on *queue*; dedupe by ``(source, external_id)`` when *source* is set."""
    _validate_payload_size(payload)
    if source is not None and not external_id:
        raise QueueValidationError('external_id is required when source is set')

    dedup_key = external_id
    if source is not None:
        if dedup_key is None:
            raise QueueValidationError('external_id is required when source is set')
        existing = QueueItem.objects.filter(source=source, external_id=dedup_key).first()
        if existing is not None:
            if existing.status == QueueItemStatus.AVAILABLE:
                _schedule_queue_dispatch_on_commit(queue.id)
            return PutResult(item_id=existing.id, created=False)
        try:
            item = QueueItem.objects.create(
                queue=queue,
                source=source,
                external_id=dedup_key,
                payload=payload,
                status=QueueItemStatus.AVAILABLE,
            )
        except IntegrityError:
            existing = QueueItem.objects.get(source=source, external_id=dedup_key)
            if existing.status == QueueItemStatus.AVAILABLE:
                _schedule_queue_dispatch_on_commit(queue.id)
            return PutResult(item_id=existing.id, created=False)
        _schedule_queue_dispatch_on_commit(queue.id)
        return PutResult(item_id=item.id, created=True)

    item = QueueItem.objects.create(
        queue=queue,
        source=source,
        external_id=external_id or '',
        payload=payload,
        status=QueueItemStatus.AVAILABLE,
    )
    _schedule_queue_dispatch_on_commit(queue.id)
    return PutResult(item_id=item.id, created=True)


@transaction.atomic
def take_item(*, queue: Queue, session_id: UUID) -> TakeResult | None:
    """Atomically claim the oldest available item; append a ``QueueItemAttempt`` row."""
    try:
        session = AgentSession.objects.get(pk=session_id)
    except AgentSession.DoesNotExist as exc:
        raise QueueValidationError(f'session not found: {session_id}') from exc
    if session.agent_id != queue.agent_id:
        raise QueueValidationError(
            f'session {session_id} does not belong to queue agent {queue.agent_id}',
        )

    while True:
        item = (
            QueueItem.objects.select_for_update(skip_locked=True)
            .filter(queue=queue, status=QueueItemStatus.AVAILABLE)
            .order_by('created_at', 'id')
            .first()
        )
        if item is None:
            return None

        next_attempt = item.attempt_count + 1
        if next_attempt > queue.max_attempts:
            item.status = QueueItemStatus.EXHAUSTED
            item.completed_at = timezone.now()
            item.save(update_fields=['status', 'completed_at'])
            continue

        now = timezone.now()
        item.status = QueueItemStatus.TAKEN
        item.attempt_count = next_attempt
        item.taken_by_session_id = session_id
        item.taken_at = now
        item.save(
            update_fields=[
                'status',
                'attempt_count',
                'taken_by_session_id',
                'taken_at',
            ],
        )
        QueueItemAttempt.objects.create(
            item=item,
            session_id=session_id,
            attempt_number=next_attempt,
            outcome=QueueItemAttemptOutcome.IN_PROGRESS,
            started_at=now,
        )
        return TakeResult(
            item_id=item.id,
            payload=item.payload,
            attempt_count=next_attempt,
        )


def _get_taken_item(*, item_id: UUID, session_id: UUID) -> QueueItem:
    """Load a taken item and verify *session_id* is the current taker."""
    try:
        item = QueueItem.objects.get(pk=item_id)
    except QueueItem.DoesNotExist as exc:
        raise QueueItemNotFoundError(f'queue item not found: {item_id}') from exc

    if item.status != QueueItemStatus.TAKEN:
        raise QueueItemStateError(f'item {item_id} is not taken')
    if item.taken_by_session_id != session_id:
        raise QueueNotTakerError(f'session {session_id} is not the taker for item {item_id}')
    return item


def _close_open_attempt(
    *,
    item: QueueItem,
    session_id: UUID,
    outcome: QueueItemAttemptOutcome,
    detail: str | None = None,
) -> None:
    """Close the open ``in_progress`` attempt for (*item*, *session_id*)."""
    now = timezone.now()
    updated = QueueItemAttempt.objects.filter(
        item=item,
        session_id=session_id,
        outcome=QueueItemAttemptOutcome.IN_PROGRESS,
    ).update(outcome=outcome, ended_at=now, detail=detail)
    if not updated:
        raise QueueItemStateError(f'no open attempt for item {item.id} and session {session_id}')


@transaction.atomic
def complete_item(*, item_id: UUID, session_id: UUID) -> None:
    """Mark a taken item ``done``; only the taker session may call this."""
    item = _get_taken_item(item_id=item_id, session_id=session_id)
    now = timezone.now()
    item.status = QueueItemStatus.DONE
    item.completed_at = now
    item.taken_by_session = None
    item.taken_at = None
    item.save(
        update_fields=[
            'status',
            'completed_at',
            'taken_by_session',
            'taken_at',
        ],
    )
    _close_open_attempt(
        item=item,
        session_id=session_id,
        outcome=QueueItemAttemptOutcome.COMPLETED,
    )


@transaction.atomic
def fail_item(*, item_id: UUID, session_id: UUID, reason: str = '') -> None:
    """Mark a taken item ``failed``; only the taker session may call this."""
    item = _get_taken_item(item_id=item_id, session_id=session_id)
    now = timezone.now()
    item.status = QueueItemStatus.FAILED
    item.failure_reason = reason
    item.completed_at = now
    item.taken_by_session = None
    item.taken_at = None
    item.save(
        update_fields=[
            'status',
            'failure_reason',
            'completed_at',
            'taken_by_session',
            'taken_at',
        ],
    )
    _close_open_attempt(
        item=item,
        session_id=session_id,
        outcome=QueueItemAttemptOutcome.FAILED,
        detail=reason or None,
    )


def _should_release_stale_item(*, item: QueueItem, now: datetime) -> tuple[bool, str | None]:
    """Return whether a taken item should be released and a detail string for the attempt log."""
    if item.taken_at is None:
        return False, None
    queue = item.queue
    held_seconds = (now - item.taken_at).total_seconds()
    if held_seconds < queue.min_hold_seconds:
        return False, None
    if held_seconds >= queue.long_hold_seconds:
        return True, 'long_hold_release'
    session = item.taken_by_session
    if session is None:
        return False, None
    if is_session_releasable(session) and held_seconds >= queue.early_release_seconds:
        return True, 'stale_release'
    return False, None


def _release_taken_item(*, item: QueueItem, now: datetime, detail: str | None) -> str:
    """Release or exhaust a taken item; returns ``released`` or ``exhausted``."""
    if item.taken_by_session_id is None:
        raise QueueItemStateError(f'item {item.id} has no taker session')
    session_id = item.taken_by_session_id
    queue = item.queue
    if item.attempt_count >= queue.max_attempts:
        item.status = QueueItemStatus.EXHAUSTED
        item.completed_at = now
        outcome = QueueItemAttemptOutcome.EXHAUSTED
        result = 'exhausted'
    else:
        item.status = QueueItemStatus.AVAILABLE
        outcome = QueueItemAttemptOutcome.RELEASED
        result = 'released'
    item.taken_by_session = None
    item.taken_at = None
    item.save(
        update_fields=[
            'status',
            'completed_at',
            'taken_by_session',
            'taken_at',
        ],
    )
    _close_open_attempt(
        item=item,
        session_id=session_id,
        outcome=outcome,
        detail=detail,
    )
    return result


def release_stale_items(*, now: datetime | None = None) -> ReleaseStats:
    """Reclaim stuck taken items per queue min/early/long hold rules."""
    if now is None:
        now = timezone.now()

    released = 0
    exhausted = 0
    taken_item_ids = list(
        QueueItem.objects.filter(status=QueueItemStatus.TAKEN).values_list('pk', flat=True),
    )
    for item_id in taken_item_ids:
        result: str | None = None
        try:
            with transaction.atomic():
                item = (
                    QueueItem.objects.select_for_update()
                    .filter(pk=item_id, status=QueueItemStatus.TAKEN)
                    .select_related('queue', 'taken_by_session')
                    .first()
                )
                if item is None:
                    continue
                should_release, detail = _should_release_stale_item(item=item, now=now)
                if not should_release:
                    continue
                result = _release_taken_item(item=item, now=now, detail=detail)
        except QueueItemStateError as exc:
            logger.warning('release_stale_items skipped item %s: %s', item_id, exc)
            continue
        if result == 'released':
            released += 1
        elif result == 'exhausted':
            exhausted += 1
    return ReleaseStats(released=released, exhausted=exhausted)


def _validate_adapter_config(adapter_type: str, config: dict[str, Any]) -> None:
    """Validate source adapter config via ``libs.sources`` when the registry is available."""
    if not adapter_type:
        raise QueueValidationError('adapter type is required')
    try:
        from libs.sources.registry import get_adapter
    except ImportError:
        return
    adapter = get_adapter(adapter_type)
    if adapter is None:
        raise QueueValidationError(f'unknown adapter type {adapter_type!r}')
    adapter.validate_config(config)


def _sync_source(queue: Queue, source_spec: SourceSpec) -> Source:
    """Create or update a ``Source`` row from a spec fragment."""
    _validate_adapter_config(source_spec.adapter_type, source_spec.config)
    source, _created = Source.objects.get_or_create(
        queue=queue,
        source_id=source_spec.id,
        defaults={
            'adapter_type': source_spec.adapter_type,
            'config': source_spec.config,
            'credential_ref': source_spec.credential_ref,
            'status': SourceStatus.ACTIVE,
        },
    )
    source.adapter_type = source_spec.adapter_type
    source.config = source_spec.config
    source.credential_ref = source_spec.credential_ref
    source.status = SourceStatus.ACTIVE
    source.save(
        update_fields=[
            'adapter_type',
            'config',
            'credential_ref',
            'status',
        ],
    )
    return source


def _remove_orphan_sources(queue: Queue, kept_source_ids: set[str]) -> None:
    """Drop sources removed from spec, or disable them when items still reference them."""
    for source in Source.objects.filter(queue=queue).exclude(source_id__in=kept_source_ids):
        if source.items.exists():
            source.status = SourceStatus.DISABLED
            source.save(update_fields=['status'])
        else:
            source.delete()


def _remove_orphan_queues(agent: Agent, kept_queue_ids: set[str]) -> None:
    """Drop queues removed from spec, or disable their sources when items remain."""
    for queue in Queue.objects.filter(agent=agent).exclude(queue_id__in=kept_queue_ids):
        if queue.items.exists():
            Source.objects.filter(queue=queue, status=SourceStatus.ACTIVE).update(
                status=SourceStatus.DISABLED,
            )
        else:
            queue.delete()


@transaction.atomic
def sync_from_spec(
    agent: Agent,
    config: AgentConfig,
    queues: list[QueueSpec],
) -> None:
    """Reconcile ``Queue`` and nested ``Source`` DB rows from optional spec ``queues[]``."""
    kept_queue_ids: set[str] = set()
    for queue_spec in queues:
        queue, _created = Queue.objects.get_or_create(
            agent=agent,
            queue_id=queue_spec.id,
            defaults={
                'agent_config': config,
                'max_attempts': queue_spec.max_attempts,
                'min_hold_seconds': queue_spec.min_hold_seconds,
                'early_release_seconds': queue_spec.early_release_seconds,
                'long_hold_seconds': queue_spec.long_hold_seconds,
            },
        )
        queue.agent_config = config
        queue.max_attempts = queue_spec.max_attempts
        queue.min_hold_seconds = queue_spec.min_hold_seconds
        queue.early_release_seconds = queue_spec.early_release_seconds
        queue.long_hold_seconds = queue_spec.long_hold_seconds
        queue.save(
            update_fields=[
                'agent_config',
                'max_attempts',
                'min_hold_seconds',
                'early_release_seconds',
                'long_hold_seconds',
            ],
        )

        kept_queue_ids.add(queue_spec.id)
        kept_source_ids: set[str] = set()
        for source_spec in queue_spec.sources:
            _sync_source(queue, source_spec)
            kept_source_ids.add(source_spec.id)
        _remove_orphan_sources(queue, kept_source_ids)

    _remove_orphan_queues(agent, kept_queue_ids)
