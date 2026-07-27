# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Low-level activity persistence for session service commands."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

TERMINAL_ACTIVITY_STATUSES = frozenset(
    {
        AgentSessionActivityStatus.SUCCEEDED,
        AgentSessionActivityStatus.FAILED,
        AgentSessionActivityStatus.CANCELLED,
    }
)
_LIFECYCLE_KINDS = frozenset({'tool', 'llm', 'span', 'subagent'})
_VALID_KINDS = frozenset(AgentSessionActivityKind.values)
_VALID_STATUSES = frozenset(AgentSessionActivityStatus.values)


class ActivityUpdateUnset:
    """Mark an update field that the caller did not provide."""


ACTIVITY_UPDATE_UNSET = ActivityUpdateUnset()


def _validate_kind(kind: str) -> None:
    """Reject activity kinds outside the canonical model vocabulary."""
    if kind not in _VALID_KINDS:
        raise ValidationError({'kind': 'invalid activity kind'})


def _validate_status(status: str) -> None:
    """Reject lifecycle statuses outside the canonical model vocabulary."""
    if status not in _VALID_STATUSES:
        raise ValidationError({'status': 'invalid activity status'})


def _next_seq(session: AgentSession) -> int:
    """Return the next sequence while the caller holds the session row lock."""
    current = AgentSessionActivity.objects.filter(session=session).aggregate(max_seq=Max('seq'))['max_seq']
    return (current or 0) + 1


def _validate_child_session_link(
    *,
    session: AgentSession,
    kind: str,
    child_session_id: UUID,
) -> AgentSession:
    """Lock and validate one direct same-owner subagent-session reference."""
    if kind != AgentSessionActivityKind.SUBAGENT:
        raise ValidationError({'child_session_id': 'only subagent activities may link a child session'})
    try:
        child = AgentSession.objects.select_for_update().select_related('agent').get(pk=child_session_id)
    except AgentSession.DoesNotExist as exc:
        raise ValidationError({'child_session_id': 'child session does not exist'}) from exc
    if child.parent_session_id != session.id:
        raise ValidationError({'child_session_id': 'child session must point directly to the activity session'})
    if child.agent.user_id != session.agent.user_id:
        raise ValidationError({'child_session_id': 'child and parent sessions must have the same owner'})
    return child


@transaction.atomic
def create_activity_row(
    session: AgentSession,
    *,
    kind: str,
    status: str,
    name: str,
    summary: str,
    details: dict[str, Any],
    parent_id: UUID | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    child_session_id: UUID | None = None,
) -> AgentSessionActivity:
    """Insert one activity after validating hierarchy and serializing sequence allocation."""
    _validate_kind(kind)
    _validate_status(status)
    # Every writer locks the same session row before reading the current maximum,
    # preventing concurrent inserts from choosing the same sequence.
    locked_session = AgentSession.objects.select_for_update().get(pk=session.pk)
    parent = None
    if parent_id is not None:
        try:
            parent = AgentSessionActivity.objects.select_for_update().get(pk=parent_id)
        except AgentSessionActivity.DoesNotExist as exc:
            raise ValidationError({'parent_id': 'parent activity not found'}) from exc
        if parent.session_id != locked_session.id:
            raise ValidationError({'parent_id': 'parent must belong to the same session'})
    if child_session_id is not None:
        _validate_child_session_link(
            session=locked_session,
            kind=kind,
            child_session_id=child_session_id,
        )

    now = timezone.now()
    if status == AgentSessionActivityStatus.RUNNING and started_at is None:
        started_at = now
    if ended_at is None and status in TERMINAL_ACTIVITY_STATUSES and kind in _LIFECYCLE_KINDS:
        ended_at = now

    return AgentSessionActivity.objects.create(
        session=locked_session,
        parent=parent,
        seq=_next_seq(locked_session),
        revision=1,
        kind=kind,
        status=status,
        name=name,
        summary=summary,
        details=details,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        started_at=started_at,
        ended_at=ended_at,
        child_session_id=child_session_id,
    )


@transaction.atomic
def update_activity_row(
    activity_id: UUID,
    *,
    status: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    model: str | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    input_tokens: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    output_tokens: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    cost_usd: Decimal | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    latency_ms: int | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    started_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
    ended_at: datetime | None | ActivityUpdateUnset = ACTIVITY_UPDATE_UNSET,
) -> AgentSessionActivity:
    """Lock and revise mutable fields while preserving child-link identity."""
    if status is not None:
        _validate_status(status)
    row = AgentSessionActivity.objects.select_for_update().get(pk=activity_id)
    if row.child_session_id is not None:
        locked_session = AgentSession.objects.select_for_update().select_related('agent').get(pk=row.session_id)
        _validate_child_session_link(
            session=locked_session,
            kind=row.kind,
            child_session_id=row.child_session_id,
        )
    if row.status in TERMINAL_ACTIVITY_STATUSES:
        raise ValidationError({'status': 'terminal activity is immutable'})

    lifecycle_updates = {
        'status': status,
        'summary': summary,
        'details': details,
    }
    for field, value in lifecycle_updates.items():
        if value is not None:
            setattr(row, field, value)

    nullable_updates: dict[str, Any] = {
        'model': model,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cost_usd': cost_usd,
        'latency_ms': latency_ms,
        'started_at': started_at,
        'ended_at': ended_at,
    }
    for field, value in nullable_updates.items():
        if value is not ACTIVITY_UPDATE_UNSET:
            setattr(row, field, value)

    if status in TERMINAL_ACTIVITY_STATUSES and row.ended_at is None:
        row.ended_at = timezone.now()
    row.revision += 1
    row.save()
    return row
