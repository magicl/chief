# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Async session metadata tasks."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from apps.agents.models import Agent
from apps.runner.llm_config import provider_config_from_spec
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    HourlyUsage,
)
from apps.sessions.services.queries import get_first_input_text, get_session_name
from celery import shared_task
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone

# isort: split

from libs.agent_spec import LLMSpec
from libs.algorithms.chat_name import (
    DEFAULT_CHAT_NAME_CONFIG,
    ChatNameConfig,
    generate_chat_name,
)

logger = logging.getLogger(__name__)

_TERMINAL_ACTIVITY_STATUSES = (
    AgentSessionActivityStatus.SUCCEEDED,
    AgentSessionActivityStatus.FAILED,
    AgentSessionActivityStatus.CANCELLED,
)


def _bucket_filter(
    buckets: set[tuple[UUID, datetime]],
    *,
    agent_field: str,
    time_field: str,
) -> Q:
    """Build an exact UTC agent/hour predicate for activity or usage rows."""
    predicate = Q(pk__in=[])
    for agent_id, hour in buckets:
        predicate |= Q(
            **{
                agent_field: agent_id,
                f'{time_field}__gte': hour,
                f'{time_field}__lt': hour + timedelta(hours=1),
            }
        )
    return predicate


@shared_task(bind=True, ignore_result=True, max_retries=2)
def generate_session_name(self: Any, session_id: str) -> None:
    uid = UUID(session_id)
    if get_session_name(uid) is not None:
        return
    text = get_first_input_text(uid)
    if text is None:
        return
    try:
        session = AgentSession.objects.select_related('agent').get(pk=uid)
        user_id = session.agent.user_id
        llm_cfg = provider_config_from_spec(
            LLMSpec(
                provider=DEFAULT_CHAT_NAME_CONFIG.provider,
                model=DEFAULT_CHAT_NAME_CONFIG.model,
                temperature=DEFAULT_CHAT_NAME_CONFIG.temperature,
            ),
            user_id=user_id,
        )
        name = generate_chat_name(text, config=DEFAULT_CHAT_NAME_CONFIG, llm=llm_cfg)
    except Exception:  # pylint: disable=broad-except
        logger.exception('Chat name generation failed for session %s', session_id)
        name = generate_chat_name(text, config=ChatNameConfig(enabled=False))
    from apps.sessions.services.commands import update_session_name

    update_session_name(uid, name)


@shared_task(ignore_result=True)
def aggregate_hourly_usage() -> None:
    """Roll up recent terminal LLM and tool activities into hourly rows.

    Uses a 2-hour lookback window and full-replaces affected hour buckets,
    making the task idempotent without needing a watermark.
    """
    cutoff = (timezone.now().astimezone(UTC) - timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    recent_transition = Q(created_at__gte=cutoff) | Q(ended_at__gte=cutoff)
    agent_ids = list(
        AgentSessionActivity.objects.filter(
            recent_transition,
            kind__in=(
                AgentSessionActivityKind.LLM,
                AgentSessionActivityKind.TOOL,
            ),
            status__in=_TERMINAL_ACTIVITY_STATUSES,
        )
        .order_by()
        .values_list('session__agent_id', flat=True)
        .distinct()
    )
    if agent_ids:
        _replace_hourly_usage(cutoff, agent_ids)


@transaction.atomic
def _replace_hourly_usage(cutoff: datetime, agent_ids: list[UUID]) -> None:
    """Serialize and atomically replace complete recent buckets for agents."""
    # Agent rows are the stable mutex shared by overlapping rollups. Build the
    # snapshot only after this lock so a waiter cannot apply an older snapshot.
    locked_agent_ids = list(
        Agent.objects.select_for_update().filter(pk__in=agent_ids).order_by('pk').values_list('pk', flat=True)
    )
    recent_transition = Q(created_at__gte=cutoff) | Q(ended_at__gte=cutoff)
    affected_buckets: set[tuple[UUID, datetime]] = set(
        AgentSessionActivity.objects.filter(
            recent_transition,
            kind__in=(
                AgentSessionActivityKind.LLM,
                AgentSessionActivityKind.TOOL,
            ),
            status__in=_TERMINAL_ACTIVITY_STATUSES,
            session__agent_id__in=locked_agent_ids,
        )
        .annotate(hour=TruncHour('created_at', tzinfo=UTC))
        .order_by()
        .values_list('session__agent_id', 'hour')
        .distinct()
    )
    activity_bucket_filter = _bucket_filter(
        affected_buckets,
        agent_field='session__agent_id',
        time_field='created_at',
    )
    llm_rows = (
        AgentSessionActivity.objects.filter(
            activity_bucket_filter,
            kind=AgentSessionActivityKind.LLM,
            status__in=_TERMINAL_ACTIVITY_STATUSES,
        )
        .annotate(hour=TruncHour('created_at', tzinfo=UTC))
        .values('session__agent_id', 'model', 'hour')
        .annotate(
            total_input_tokens=Sum('input_tokens'),
            total_output_tokens=Sum('output_tokens'),
            total_cost=Sum('cost_usd'),
            iteration_count=Count('id'),
        )
    )

    tool_rows = (
        AgentSessionActivity.objects.filter(
            activity_bucket_filter,
            kind=AgentSessionActivityKind.TOOL,
            status__in=_TERMINAL_ACTIVITY_STATUSES,
        )
        .annotate(hour=TruncHour('created_at', tzinfo=UTC))
        .values('session__agent_id', 'hour')
        .annotate(tool_call_count=Count('id'))
    )

    tool_counts = {(row['session__agent_id'], row['hour']): row['tool_call_count'] for row in tool_rows}
    usage_by_bucket: dict[tuple[UUID, Any], dict[str, dict[str, Any]]] = {}
    for row in llm_rows:
        key = (row['session__agent_id'], row['hour'])
        model = row['model'] or ''
        usage_by_bucket.setdefault(key, {})[model] = row

    # Tool-only hours use the empty model sentinel. When LLM rows exist, attach
    # the agent/hour tool count to one deterministic model row exactly once.
    all_buckets = affected_buckets
    # Lock existing rows before any delete/update/create so a storage failure
    # rolls back the full replacement and concurrent rollups cannot interleave.
    list(
        HourlyUsage.objects.select_for_update()
        .filter(
            _bucket_filter(
                affected_buckets,
                agent_field='agent_id',
                time_field='hour',
            )
        )
        .order_by('agent_id', 'hour', 'model')
    )
    for agent_id, hour in all_buckets:
        models = usage_by_bucket.get((agent_id, hour), {})
        desired_models = sorted(models) or ['']
        HourlyUsage.objects.filter(agent_id=agent_id, hour=hour).exclude(model__in=desired_models).delete()
        for index, model in enumerate(desired_models):
            usage_row = models.get(model)
            HourlyUsage.objects.update_or_create(
                agent_id=agent_id,
                hour=hour,
                model=model,
                defaults={
                    'input_tokens': (usage_row['total_input_tokens'] or 0) if usage_row else 0,
                    'output_tokens': (usage_row['total_output_tokens'] or 0) if usage_row else 0,
                    'cost_usd': (usage_row['total_cost'] or 0) if usage_row else 0,
                    'iteration_count': usage_row['iteration_count'] if usage_row else 0,
                    'tool_call_count': tool_counts.get((agent_id, hour), 0) if index == 0 else 0,
                },
            )
