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
from apps.runner.activity_recorder import BackendActivityRecorder
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.llm_config import provider_config_from_spec
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    HourlyUsage,
)
from apps.sessions.services.budget import user_rolling_cap_reached
from apps.sessions.services.commands import (
    create_activity,
    create_algorithm_session,
    finish_algorithm_session,
    update_activity,
    update_session_name,
)
from apps.sessions.services.queries import (
    get_algorithm_session_for_target,
    get_first_input_text,
)
from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone

# isort: split

from libs.agent_spec import LLMSpec
from libs.algorithms import (
    CHAT_NAME_ID,
    DEFAULT_CHAT_NAME_CONFIG,
    ChatNameResult,
    generate_chat_name,
)

logger = logging.getLogger(__name__)

_TERMINAL_ACTIVITY_STATUSES = (
    AgentSessionActivityStatus.SUCCEEDED,
    AgentSessionActivityStatus.FAILED,
    AgentSessionActivityStatus.CANCELLED,
)


def _bucket_filter(
    buckets: set[tuple[Any, ...]],
    *,
    owner_fields: tuple[str, ...],
    time_field: str,
) -> Q:
    """Build an exact UTC owner/hour predicate for activity or usage rows.

    Each bucket is a tuple of owner key values in ``owner_fields`` order followed
    by the bucket's UTC hour start, so the same helper serves agent-owned rows
    (``agent_id``) and algorithm-owned rows (``user_id`` + ``algorithm_id``).
    """
    predicate = Q(pk__in=[])
    for bucket in buckets:
        *owner_values, hour = bucket
        predicate |= Q(
            **dict(zip(owner_fields, owner_values, strict=True)),
            **{
                f'{time_field}__gte': hour,
                f'{time_field}__lt': hour + timedelta(hours=1),
            },
        )
    return predicate


def _collect_bucket_usage(
    cutoff: datetime,
    *,
    owner_fields: tuple[str, ...],
    scope: Q,
) -> tuple[
    set[tuple[Any, ...]],
    dict[tuple[Any, ...], dict[str, dict[str, Any]]],
    dict[tuple[Any, ...], int],
]:
    """Summarize recent terminal activity into per-owner hourly aggregates.

    ``owner_fields`` name owner columns relative to AgentSessionActivity (e.g.
    ``session__agent_id``); ``scope`` must restrict discovery to owners the
    caller already locked, so the snapshot cannot widen past the mutex. Returns
    the affected ``(*owner values, hour)`` buckets, the per-bucket LLM
    aggregates keyed by model, and the per-bucket terminal tool-call counts.
    """
    recent_transition = Q(created_at__gte=cutoff) | Q(ended_at__gte=cutoff)
    buckets: set[tuple[Any, ...]] = set(
        AgentSessionActivity.objects.filter(
            recent_transition,
            scope,
            kind__in=(
                AgentSessionActivityKind.LLM,
                AgentSessionActivityKind.TOOL,
            ),
            status__in=_TERMINAL_ACTIVITY_STATUSES,
        )
        .annotate(hour=TruncHour('created_at', tzinfo=UTC))
        .order_by()
        .values_list(*owner_fields, 'hour')
        .distinct()
    )
    activity_bucket_filter = _bucket_filter(
        buckets,
        owner_fields=owner_fields,
        time_field='created_at',
    )
    llm_rows = (
        AgentSessionActivity.objects.filter(
            activity_bucket_filter,
            kind=AgentSessionActivityKind.LLM,
            status__in=_TERMINAL_ACTIVITY_STATUSES,
        )
        .annotate(hour=TruncHour('created_at', tzinfo=UTC))
        .values(*owner_fields, 'model', 'hour')
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
        .values(*owner_fields, 'hour')
        .annotate(tool_call_count=Count('id'))
    )

    def bucket_key(row: dict[str, Any]) -> tuple[Any, ...]:
        """Key one grouped row by its owner values plus UTC hour."""
        return (*(row[field] for field in owner_fields), row['hour'])

    tool_counts = {bucket_key(row): row['tool_call_count'] for row in tool_rows}
    usage_by_bucket: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for row in llm_rows:
        usage_by_bucket.setdefault(bucket_key(row), {})[row['model'] or ''] = row
    return buckets, usage_by_bucket, tool_counts


@shared_task(bind=True, ignore_result=True, max_retries=2)
def generate_session_name(self: Any, session_id: str) -> None:
    """Name a chat from its first message inside a ``chat_name`` algorithm session.

    The naming run is owned by a background algorithm session, so its llm
    activity and cost belong to the user's ``chat_name`` bucket instead of the
    chat's agent; the chat itself is only patched with the resulting name.
    Setup serializes on the chat row and commits before any provider call so
    user input is not blocked on LLM RTT. The provider runs while the
    algorithm session row is locked, then the llm activity commits before
    either name write. Retries that see an llm row never bill again.
    """
    del self
    uid = UUID(session_id)
    text = get_first_input_text(uid)
    if text is None:
        return
    prepared = _prepare_naming_run(uid, text)
    if prepared is None:
        return
    algorithm_session, root_id, user_id, pending_provider = prepared
    if pending_provider:
        _bill_naming_llm(
            algorithm_session,
            root_id=root_id,
            text=text,
            user_id=user_id,
        )
        algorithm_session.refresh_from_db()
    title = _existing_naming_title(algorithm_session, fallback=text) or text
    _finish_naming_run(algorithm_session, root_id=root_id, name=title)
    update_session_name(uid, title)


def _prepare_naming_run(
    uid: UUID,
    text: str,
) -> tuple[AgentSession, UUID, int, bool] | None:
    """Create or reuse the algorithm session under a short chat-row lock.

    Commits before returning so later provider work cannot roll back setup.
    The last flag is True when this worker still needs a provider call.
    """
    with transaction.atomic():
        chat = AgentSession.objects.select_for_update().only('id', 'user_id', 'name').get(pk=uid)
        if chat.name is not None:
            return None
        algorithm_session = get_algorithm_session_for_target(
            chat.user_id,
            CHAT_NAME_ID,
            uid,
        ) or create_algorithm_session(user_id=chat.user_id, algorithm_id=CHAT_NAME_ID)
        root_id = _naming_root_activity_id(algorithm_session, target_session_id=uid)
        if _existing_naming_title(algorithm_session, fallback=text) is not None:
            return algorithm_session, root_id, chat.user_id, False
        if user_rolling_cap_reached(chat.user_id):
            _record_capped_llm_activity(algorithm_session, root_id=root_id)
            return algorithm_session, root_id, chat.user_id, False
        return algorithm_session, root_id, chat.user_id, True


def _bill_naming_llm(
    session: AgentSession,
    *,
    root_id: UUID,
    text: str,
    user_id: int,
) -> None:
    """Call the provider and commit the llm row before any session name write.

    Locks the algorithm session (not the chat) so a concurrent retry waits
    rather than issuing a second completion. The chat row stays free for
    first-turn input.
    """
    with transaction.atomic():
        locked = AgentSession.objects.select_for_update().get(pk=session.pk)
        if _existing_naming_title(locked, fallback=text) is not None:
            return
        result = _generate_name_with_traces(
            locked,
            root_id=root_id,
            text=text,
            user_id=user_id,
        )
        _ensure_llm_activity(locked, root_id=root_id, result=result)


def _existing_naming_title(session: AgentSession, *, fallback: str) -> str | None:
    """Return a title already produced for this naming run, if any.

    Retries after a successful provider call must reuse this instead of billing
    a second LLM completion. Prefer the algorithm session name, then a succeeded
    llm summary, then ``fallback`` when any llm row already exists.
    """
    if not session.activities.filter(kind=AgentSessionActivityKind.LLM).exists():
        return None
    if session.name:
        return session.name
    summary = (
        session.activities.filter(
            kind=AgentSessionActivityKind.LLM,
            status=AgentSessionActivityStatus.SUCCEEDED,
        )
        .exclude(summary='')
        .order_by('seq')
        .values_list('summary', flat=True)
        .first()
    )
    return summary or fallback


def _naming_root_activity_id(session: AgentSession, *, target_session_id: UUID) -> UUID:
    """Return the run's root span, creating it with its target on first attempt.

    ``details.target_session_id`` is the only link back to the chat being
    named, so it must exist before any provider work for a retry to find this
    session again.
    """
    existing = session.activities.filter(parent_id=None).order_by('seq').values_list('id', flat=True).first()
    if existing is not None:
        return existing
    return create_activity(
        session,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.RUNNING,
        name=CHAT_NAME_ID,
        summary='Naming chat session',
        details={'target_session_id': str(target_session_id)},
    ).id


def _generate_name_with_traces(
    session: AgentSession,
    *,
    root_id: UUID,
    text: str,
    user_id: int,
) -> ChatNameResult:
    """Call the naming algorithm with its llm trace recorded under the root span.

    Credential or provider setup failures fall back to the raw first message so
    the chat still gets a name; the caller records the failed llm activity.
    """
    recorder = BackendActivityRecorder(DjangoSessionBackend(session))
    try:
        llm_cfg = provider_config_from_spec(
            LLMSpec(
                provider=DEFAULT_CHAT_NAME_CONFIG.provider,
                model=DEFAULT_CHAT_NAME_CONFIG.model,
                temperature=DEFAULT_CHAT_NAME_CONFIG.temperature,
            ),
            user_id=user_id,
        )
        with recorder.push_parent(root_id):
            return generate_chat_name(
                text,
                config=DEFAULT_CHAT_NAME_CONFIG,
                llm=llm_cfg,
                recorder=recorder,
            )
    except Exception:  # pylint: disable=broad-except
        logger.exception('Chat name generation failed for session %s', session.id)
        return ChatNameResult(title=text, provider_failed=True)


def _record_capped_llm_activity(session: AgentSession, *, root_id: UUID) -> None:
    """Record the skipped provider call so the run's cost story stays visible."""
    create_activity(
        session,
        kind=AgentSessionActivityKind.LLM,
        status=AgentSessionActivityStatus.FAILED,
        name=DEFAULT_CHAT_NAME_CONFIG.model,
        summary='User rolling spend cap reached',
        details={
            'code': 'user_spend_cap_reached',
            'provider': DEFAULT_CHAT_NAME_CONFIG.provider,
        },
        parent_id=root_id,
    )


def _ensure_llm_activity(
    session: AgentSession,
    *,
    root_id: UUID,
    result: ChatNameResult,
) -> None:
    """Persist usage from the result when the algorithm recorded none itself.

    ``generate_chat_name`` normally writes the llm activity through the
    injected recorder; this keeps tokens and cost on the session for paths that
    returned a title without reaching the recorder (setup failure, or a
    fallback taken before the provider call).
    """
    if session.activities.filter(kind=AgentSessionActivityKind.LLM).exists():
        return
    usage = result.usage
    create_activity(
        session,
        kind=AgentSessionActivityKind.LLM,
        status=(AgentSessionActivityStatus.FAILED if result.provider_failed else AgentSessionActivityStatus.SUCCEEDED),
        name=result.model or DEFAULT_CHAT_NAME_CONFIG.model,
        summary=result.title,
        details={'provider': DEFAULT_CHAT_NAME_CONFIG.provider},
        parent_id=root_id,
        model=result.model,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )


def _finish_naming_run(session: AgentSession, *, root_id: UUID, name: str | None) -> None:
    """Close the root span and the algorithm session for one naming attempt.

    Retries skip the root update when the span is already terminal so a second
    pass can still name the chat without rewriting history.
    """
    root = AgentSessionActivity.objects.filter(pk=root_id).only('status').first()
    if root is not None and root.status not in _TERMINAL_ACTIVITY_STATUSES:
        update_activity(
            root_id,
            status=AgentSessionActivityStatus.SUCCEEDED,
            summary=name or 'Chat already named',
            ended_at=timezone.now(),
        )
    finish_algorithm_session(session, name=name)


@shared_task(ignore_result=True)
def aggregate_hourly_usage() -> None:
    """Roll up recent terminal LLM and tool activities into hourly rows.

    Uses a 2-hour lookback window and full-replaces affected hour buckets,
    making the task idempotent without needing a watermark. Agent-owned and
    algorithm-owned sessions roll up separately: agent work lands on
    ``(agent, hour, model)`` rows, background algorithm work on
    ``(user, algorithm_id, hour, model)`` rows with a null agent, so algorithm
    cost is never attributed to the agent whose chat triggered it.
    """
    cutoff = (timezone.now().astimezone(UTC) - timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    recent_transition = Q(created_at__gte=cutoff) | Q(ended_at__gte=cutoff)
    recent_terminal = AgentSessionActivity.objects.filter(
        recent_transition,
        kind__in=(
            AgentSessionActivityKind.LLM,
            AgentSessionActivityKind.TOOL,
        ),
        status__in=_TERMINAL_ACTIVITY_STATUSES,
    )
    agent_ids = list(
        recent_terminal.filter(session__agent_id__isnull=False)
        .order_by()
        .values_list('session__agent_id', flat=True)
        .distinct()
    )
    user_ids = list(
        recent_terminal.filter(session__algorithm_id__isnull=False)
        .order_by()
        .values_list('session__user_id', flat=True)
        .distinct()
    )
    if agent_ids:
        _replace_hourly_usage(cutoff, agent_ids)
    if user_ids:
        _replace_algorithm_hourly_usage(cutoff, user_ids)


@transaction.atomic
def _replace_hourly_usage(cutoff: datetime, agent_ids: list[UUID]) -> None:
    """Serialize and atomically replace complete recent buckets for agents.

    Only agent-owned sessions are considered; algorithm sessions carry a null
    agent and are rolled up by _replace_algorithm_hourly_usage instead.
    """
    # Agent rows are the stable mutex shared by overlapping rollups. Build the
    # snapshot only after this lock so a waiter cannot apply an older snapshot.
    locked_agents = list(
        Agent.objects.select_for_update().filter(pk__in=agent_ids).order_by('pk').values_list('pk', 'user_id')
    )
    locked_agent_ids = [agent_id for agent_id, _user_id in locked_agents]
    # An agent session's user always matches its agent's user (enforced by
    # AgentSession validation), so the locked agent row is the usage owner.
    user_by_agent = dict(locked_agents)
    affected_buckets, usage_by_bucket, tool_counts = _collect_bucket_usage(
        cutoff,
        owner_fields=('session__agent_id',),
        scope=Q(session__agent_id__in=locked_agent_ids),
    )

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
                owner_fields=('agent_id',),
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
                    'user_id': user_by_agent[agent_id],
                    'input_tokens': (usage_row['total_input_tokens'] or 0) if usage_row else 0,
                    'output_tokens': (usage_row['total_output_tokens'] or 0) if usage_row else 0,
                    'cost_usd': (usage_row['total_cost'] or 0) if usage_row else 0,
                    'iteration_count': usage_row['iteration_count'] if usage_row else 0,
                    'tool_call_count': tool_counts.get((agent_id, hour), 0) if index == 0 else 0,
                },
            )


@transaction.atomic
def _replace_algorithm_hourly_usage(cutoff: datetime, user_ids: list[int]) -> None:
    """Serialize and atomically replace recent user+algorithm hourly buckets.

    Background algorithm sessions have no owning agent, so their spend belongs
    to the user that triggered them. User rows are the mutex here, mirroring the
    Agent lock used for agent-owned rollups: the snapshot is built only after the
    lock so a waiter cannot apply a stale one. Written rows keep ``agent_id``
    null, which is what keeps algorithm cost off the chat agent's usage rows.
    """
    locked_user_ids = list(
        get_user_model().objects.select_for_update().filter(pk__in=user_ids).order_by('pk').values_list('pk', flat=True)
    )
    affected_buckets, usage_by_bucket, tool_counts = _collect_bucket_usage(
        cutoff,
        owner_fields=('session__user_id', 'session__algorithm_id'),
        scope=Q(session__user_id__in=locked_user_ids, session__algorithm_id__isnull=False),
    )
    # Lock existing rows before any delete/update/create so a storage failure
    # rolls back the full replacement and concurrent rollups cannot interleave.
    list(
        HourlyUsage.objects.select_for_update()
        .filter(
            _bucket_filter(
                affected_buckets,
                owner_fields=('user_id', 'algorithm_id'),
                time_field='hour',
            )
        )
        .order_by('user_id', 'algorithm_id', 'hour', 'model')
    )
    for user_id, algorithm_id, hour in affected_buckets:
        models = usage_by_bucket.get((user_id, algorithm_id, hour), {})
        desired_models = sorted(models) or ['']
        # Scoped by a non-null algorithm_id, so agent rows for the same user
        # and hour are never touched.
        HourlyUsage.objects.filter(
            user_id=user_id,
            algorithm_id=algorithm_id,
            hour=hour,
        ).exclude(model__in=desired_models).delete()
        for index, model in enumerate(desired_models):
            usage_row = models.get(model)
            HourlyUsage.objects.update_or_create(
                user_id=user_id,
                algorithm_id=algorithm_id,
                hour=hour,
                model=model,
                defaults={
                    'agent_id': None,
                    'input_tokens': (usage_row['total_input_tokens'] or 0) if usage_row else 0,
                    'output_tokens': (usage_row['total_output_tokens'] or 0) if usage_row else 0,
                    'cost_usd': (usage_row['total_cost'] or 0) if usage_row else 0,
                    'iteration_count': usage_row['iteration_count'] if usage_row else 0,
                    'tool_call_count': tool_counts.get((user_id, algorithm_id, hour), 0) if index == 0 else 0,
                },
            )
