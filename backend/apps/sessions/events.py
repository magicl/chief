# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Append-only session event log (single-writer: runner only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from apps.sessions.models import AgentSession, AgentSessionEvent, AgentSessionEventKind
from django.db import transaction
from django.db.models import Max


def _next_seq(session: AgentSession) -> int:
    current = AgentSessionEvent.objects.filter(session=session).aggregate(m=Max('seq'))['m']
    return (current or 0) + 1


@transaction.atomic
def append_event(
    session: AgentSession,
    kind: str | AgentSessionEventKind,
    payload: dict[str, Any],
    *,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
) -> AgentSessionEvent:
    """Allocate the next ``seq`` and persist one event row."""
    seq = _next_seq(session)
    return AgentSessionEvent.objects.create(
        session=session,
        seq=seq,
        kind=kind,
        payload=payload,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def events_for(session: AgentSession | UUID) -> list[AgentSessionEvent]:
    if isinstance(session, UUID):
        qs = AgentSessionEvent.objects.filter(session_id=session)
    else:
        qs = AgentSessionEvent.objects.filter(session=session)
    return list(qs.order_by('seq'))
