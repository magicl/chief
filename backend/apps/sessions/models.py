# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent session models and append-only event log."""

from typing import Any

from apps.agents.models import Agent, AgentConfig
from django.db import models

from olib.py.utils.uuid7 import uuid7


class AgentSessionStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Running'
    WAITING = 'waiting', 'Waiting'
    PAUSED = 'paused', 'Paused'
    DONE = 'done', 'Done'


class TriggerType(models.TextChoices):
    TRIGGER = 'trigger', 'Trigger'
    TOOL_CALL = 'tool_call', 'Tool call'


class AgentSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='sessions')
    agent_config = models.ForeignKey(AgentConfig, on_delete=models.CASCADE, related_name='sessions')
    status = models.CharField(
        max_length=32,
        choices=AgentSessionStatus.choices,
        default=AgentSessionStatus.QUEUED,
    )
    trigger_type = models.CharField(max_length=32, choices=TriggerType.choices)
    trigger_ref = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=80, null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['agent', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.agent.identifier} session {self.id}'


class AgentSessionEventKind(models.TextChoices):
    OUTPUT = 'OUTPUT', 'Output'
    INPUT = 'INPUT', 'Input'
    TOOL_CALL = 'TOOL_CALL', 'Tool call'
    TOOL_RESULT = 'TOOL_RESULT', 'Tool result'
    FAILURE = 'FAILURE', 'Failure'
    RESTART = 'RESTART', 'Restart'


class AgentSessionEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='events')
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=32, choices=AgentSessionEventKind.choices)
    payload = models.JSONField(default=dict)
    model = models.CharField(max_length=255, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['session', 'seq'], name='sessions_event_session_seq_uniq'),
        ]
        indexes = [
            models.Index(fields=['session', 'seq']),
            models.Index(fields=['session', 'created_at']),
        ]
        ordering = ['seq']

    def __str__(self) -> str:
        return f'{self.session_id}#{self.seq} {self.kind}'

    def to_stream_dict(self) -> dict[str, Any]:
        """Serialize for SSE / pub-sub transport."""
        return {
            'id': str(self.id),
            'session_id': str(self.session_id),
            'seq': self.seq,
            'kind': self.kind,
            'payload': self.payload,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost_usd': str(self.cost_usd) if self.cost_usd is not None else None,
            'latency_ms': self.latency_ms,
            'created_at': self.created_at.isoformat(),
        }


class HourlyUsage(models.Model):
    """Pre-aggregated token and spend totals per agent per model per hour.

    Populated by a periodic celery task that rolls up AgentSessionEvent rows.
    Consumed by budget-check queries (daily/monthly spend sums).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='hourly_usage')
    hour = models.DateTimeField()
    model = models.CharField(max_length=255)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    cache_creation_input_tokens = models.PositiveBigIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    iteration_count = models.PositiveIntegerField(default=0)
    tool_call_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['agent', 'hour', 'model'],
                name='sessions_hourlyusage_agent_hour_model_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['agent', 'hour']),
        ]

    def __str__(self) -> str:
        return f'HourlyUsage({self.agent_id}, {self.hour}, {self.model})'
