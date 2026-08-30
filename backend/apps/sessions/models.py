# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent session and hierarchical activity models."""

from typing import Any, cast
from uuid import UUID

from apps.agents.models import Agent, AgentConfig
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from libs.algorithms import get_algorithm

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
    ALGORITHM = 'algorithm', 'Algorithm'


class AgentSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='agent_sessions',
    )
    # Agent-only call sites branch by owner mode before dereferencing. Keep the
    # established relation type while the database permits algorithm-owned nulls.
    agent: Agent = cast(
        Any,
        models.ForeignKey(
            Agent,
            null=True,
            blank=True,
            on_delete=models.CASCADE,
            related_name='sessions',
        ),
    )
    agent_config: AgentConfig = cast(
        Any,
        models.ForeignKey(
            AgentConfig,
            null=True,
            blank=True,
            on_delete=models.CASCADE,
            related_name='sessions',
        ),
    )
    algorithm_id = models.CharField(max_length=64, null=True, blank=True)
    parent_session = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='child_sessions',
    )
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(agent__isnull=False, agent_config__isnull=False, algorithm_id__isnull=True)
                    | models.Q(
                        agent__isnull=True,
                        agent_config__isnull=True,
                        algorithm_id__isnull=False,
                        parent_session__isnull=True,
                    )
                ),
                name='sessions_agentsession_owner_xor',
            ),
        ]
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['agent', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'algorithm_id', '-created_at']),
            models.Index(fields=['parent_session']),
        ]

    def _validate_locked_ancestry(self) -> None:
        """Validate immutable, same-owner acyclic ancestry while locking its chain."""
        if self.agent_config_id is None or self.agent_config.agent_id != self.agent_id:
            raise ValidationError({'agent_config': 'agent config must belong to the session agent'})

        if self.parent_session_id is None:
            return
        if self.parent_session_id == self.id:
            raise ValidationError({'parent_session': 'a session cannot parent itself'})

        child_user_id = self.user_id
        seen = {self.id}
        current_id: UUID | None = self.parent_session_id
        while current_id is not None:
            if current_id in seen:
                raise ValidationError({'parent_session': 'session ancestry cannot contain a cycle'})
            seen.add(current_id)
            try:
                current = (
                    AgentSession.objects.select_for_update()
                    .only('id', 'parent_session_id', 'user_id')
                    .get(pk=current_id)
                )
            except AgentSession.DoesNotExist as exc:
                raise ValidationError({'parent_session': 'parent session does not exist'}) from exc
            if current.user_id != child_user_id:
                raise ValidationError({'parent_session': 'parent and child sessions must have the same owner'})
            current_id = current.parent_session_id

    def _validate_owner(self) -> None:
        """Validate the exact agent-owned or registered algorithm-owned mode."""
        if self.user_id is None:
            raise ValidationError({'user': 'session user is required'})
        if self.agent_id is not None and self.algorithm_id is None:
            if self.agent_config_id is None:
                raise ValidationError({'agent_config': 'agent-owned sessions require an agent config'})
            if self.agent.user_id != self.user_id:
                raise ValidationError({'user': 'session user must match the agent owner'})
            self._validate_locked_ancestry()
            return
        if self.algorithm_id is not None and self.agent_id is None and self.agent_config_id is None:
            failures: dict[str, str] = {}
            if self.parent_session_id is not None:
                failures['parent_session'] = 'algorithm sessions cannot have parent sessions'
            if self.trigger_type != TriggerType.ALGORITHM:
                failures['trigger_type'] = 'algorithm sessions require the algorithm trigger type'
            if get_algorithm(self.algorithm_id) is None:
                failures['algorithm_id'] = 'algorithm must be registered'
            if failures:
                raise ValidationError(failures)
            return
        raise ValidationError({'agent': 'session must have exactly one agent or algorithm owner'})

    @transaction.atomic
    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist while freezing owner/config/ancestry and skipping pure state saves."""
        if self._state.adding and self.user_id is None and self.agent_id is not None:
            self.user = self.agent.user  # pylint: disable=no-member
        update_fields = kwargs.get('update_fields')
        immutable_fields = frozenset(
            {
                'user',
                'user_id',
                'agent',
                'agent_id',
                'agent_config',
                'agent_config_id',
                'algorithm_id',
                'parent_session',
                'parent_session_id',
            }
        )
        if not self._state.adding and update_fields is not None and immutable_fields.isdisjoint(update_fields):
            super().save(*args, **kwargs)
            return
        if not self._state.adding:
            try:
                persisted = (
                    AgentSession.objects.select_for_update()
                    .only('user_id', 'agent_id', 'agent_config_id', 'algorithm_id', 'parent_session_id')
                    .get(pk=self.pk)
                )
            except AgentSession.DoesNotExist:
                persisted = None
            if persisted is not None:
                failures: dict[str, str] = {}
                if persisted.user_id != self.user_id:
                    failures['user'] = 'session user is immutable after creation'
                if persisted.agent_id != self.agent_id:
                    failures['agent'] = 'session owner is immutable after creation'
                if persisted.agent_config_id != self.agent_config_id:
                    failures['agent_config'] = 'session config is immutable after creation'
                if persisted.algorithm_id != self.algorithm_id:
                    failures['algorithm_id'] = 'session algorithm is immutable after creation'
                if persisted.parent_session_id != self.parent_session_id:
                    failures['parent_session'] = 'session ancestry is immutable after creation'
                if failures:
                    raise ValidationError(failures)
        self._validate_owner()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        owner = self.agent.identifier if self.agent_id is not None else self.algorithm_id  # pylint: disable=no-member
        return f'{owner} session {self.id}'


class AgentSessionActivityKind(models.TextChoices):
    INPUT = 'input', 'Input'
    OUTPUT = 'output', 'Output'
    TOOL = 'tool', 'Tool'
    LLM = 'llm', 'LLM'
    SPAN = 'span', 'Span'
    STATUS = 'status', 'Status'
    SUBAGENT = 'subagent', 'Sub-agent'
    FAILURE = 'failure', 'Failure'
    RESTART = 'restart', 'Restart'


class AgentSessionActivityStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


class AgentSessionActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='activities')
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children',
    )
    seq = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=32, choices=AgentSessionActivityKind.choices)
    status = models.CharField(max_length=32, choices=AgentSessionActivityStatus.choices)
    name = models.CharField(max_length=255)
    summary = models.CharField(max_length=512, blank=True, default='')
    details = models.JSONField(default=dict)
    model = models.CharField(max_length=255, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    child_session = models.OneToOneField(
        AgentSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parent_activity',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'seq'],
                name='sessions_activity_session_seq_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['session', 'seq']),
            models.Index(fields=['session', 'created_at']),
            models.Index(fields=['parent']),
            models.Index(fields=['kind', 'status', 'created_at']),
            models.Index(fields=['kind', 'status', 'ended_at']),
        ]
        ordering = ['seq']

    def to_stream_dict(self) -> dict[str, Any]:
        """Serialize the complete activity into JSON-safe SSE values."""
        return {
            'id': str(self.id),
            'session_id': str(self.session_id),
            'parent_id': str(self.parent_id) if self.parent_id else None,
            'seq': self.seq,
            'revision': self.revision,
            'kind': self.kind,
            'status': self.status,
            'name': self.name,
            'summary': self.summary,
            'details': self.details,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost_usd': str(self.cost_usd) if self.cost_usd is not None else None,
            'latency_ms': self.latency_ms,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'created_at': self.created_at.isoformat(),
            'child_session_id': str(self.child_session_id) if self.child_session_id else None,
        }


class HourlyUsage(models.Model):
    """Pre-aggregated token and spend totals per owner per model per hour.

    Populated by a periodic celery task that rolls up AgentSessionActivity rows.
    Consumed by budget-check queries (daily/monthly spend sums).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hourly_usage',
    )
    agent = models.ForeignKey(
        Agent,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='hourly_usage',
    )
    algorithm_id = models.CharField(max_length=64, null=True, blank=True)
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
            models.CheckConstraint(
                condition=(
                    models.Q(agent__isnull=False, algorithm_id__isnull=True)
                    | models.Q(agent__isnull=True, algorithm_id__isnull=False)
                ),
                name='sessions_hourlyusage_owner_xor',
            ),
            models.UniqueConstraint(
                fields=['agent', 'hour', 'model'],
                condition=models.Q(agent__isnull=False),
                name='sessions_hourlyusage_agent_hour_model_uniq',
            ),
            models.UniqueConstraint(
                fields=['user', 'algorithm_id', 'hour', 'model'],
                condition=models.Q(algorithm_id__isnull=False),
                name='sessions_hourlyusage_algorithm_hour_model_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['agent', 'hour']),
            models.Index(fields=['user', 'algorithm_id', 'hour']),
        ]

    def _validate_owner(self) -> None:
        """Validate one agent or registered algorithm owner before persistence."""
        if self.user_id is None:
            raise ValidationError({'user': 'usage user is required'})
        if self.agent_id is not None and self.algorithm_id is None:
            return
        if self.agent_id is None and self.algorithm_id is not None:
            if get_algorithm(self.algorithm_id) is None:
                raise ValidationError({'algorithm_id': 'algorithm must be registered'})
            return
        raise ValidationError({'agent': 'usage must have exactly one agent or algorithm owner'})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist only usage rows with a valid owner mode."""
        self._validate_owner()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        owner = self.algorithm_id if self.agent_id is None else str(self.agent_id)
        return f'HourlyUsage({owner}, {self.hour}, {self.model})'
