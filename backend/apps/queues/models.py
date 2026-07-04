# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Queue, source, item, and attempt history models."""

from __future__ import annotations

from apps.agents.models import Agent, AgentConfig
from apps.sessions.models import AgentSession
from django.db import models
from django.db.models import Q

from olib.py.utils.uuid7 import uuid7


class SourceStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'


class QueueItemStatus(models.TextChoices):
    AVAILABLE = 'available', 'Available'
    TAKEN = 'taken', 'Taken'
    DONE = 'done', 'Done'
    FAILED = 'failed', 'Failed'
    EXHAUSTED = 'exhausted', 'Exhausted'


class QueueItemAttemptOutcome(models.TextChoices):
    IN_PROGRESS = 'in_progress', 'In progress'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    RELEASED = 'released', 'Released'
    EXHAUSTED = 'exhausted', 'Exhausted'


class Queue(models.Model):
    """Agent-scoped work queue; settings synced from ``AgentConfigSpec.queues[]``."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='queues')
    queue_id = models.CharField(max_length=64)
    agent_config = models.ForeignKey(
        AgentConfig,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='queues',
    )
    max_attempts = models.PositiveSmallIntegerField(default=3)
    min_hold_seconds = models.PositiveIntegerField(default=60)
    early_release_seconds = models.PositiveIntegerField(default=300)
    long_hold_seconds = models.PositiveIntegerField(default=3600)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['agent', 'queue_id'],
                name='queues_queue_agent_queue_id_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['agent', 'queue_id']),
        ]

    def __str__(self) -> str:
        return f'{self.agent.identifier}:{self.queue_id}'


class Source(models.Model):
    """Inbound adapter config nested under a queue; polls enqueue deduped items."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='sources')
    source_id = models.CharField(max_length=64)
    adapter_type = models.CharField(max_length=64)
    config = models.JSONField(default=dict)
    status = models.CharField(
        max_length=32,
        choices=SourceStatus.choices,
        default=SourceStatus.ACTIVE,
    )
    credential_ref = models.CharField(max_length=255, null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['queue', 'source_id'],
                name='queues_source_queue_source_id_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['queue', 'source_id']),
        ]

    def __str__(self) -> str:
        return f'{self.queue}:{self.source_id}'


class QueueItem(models.Model):
    """One unit of work on a queue; ``taken_by_session`` is the current taker only."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='items')
    source = models.ForeignKey(
        Source,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='items',
    )
    external_id = models.CharField(max_length=255, default='')
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=32,
        choices=QueueItemStatus.choices,
        default=QueueItemStatus.AVAILABLE,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    taken_by_session = models.ForeignKey(
        AgentSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='taken_queue_items',
    )
    taken_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'external_id'],
                condition=Q(source__isnull=False),
                name='queues_queueitem_source_external_id_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['queue', 'status', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.queue_id} item {self.id}'


class QueueItemAttempt(models.Model):
    """Append-only record of one session take; retained for retry debugging."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    item = models.ForeignKey(QueueItem, on_delete=models.CASCADE, related_name='attempts')
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='queue_item_attempts')
    attempt_number = models.PositiveIntegerField()
    outcome = models.CharField(
        max_length=32,
        choices=QueueItemAttemptOutcome.choices,
        default=QueueItemAttemptOutcome.IN_PROGRESS,
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['item', 'attempt_number']),
            models.Index(fields=['item', 'session']),
        ]
        ordering = ['attempt_number']

    def __str__(self) -> str:
        return f'{self.item_id} attempt {self.attempt_number}'
