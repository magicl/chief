# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Budget query helpers for session limit enforcement.

Provides efficient spend lookups against the pre-aggregated HourlyUsage table
and the effective spend cap computation that collapses rolling budgets into a
single in-memory comparison value.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from apps.sessions.models import HourlyUsage
from django.db.models import Sum
from django.utils import timezone


def agent_daily_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def agent_monthly_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def user_daily_spend(user_id: int) -> Decimal:
    """Sum spend across all agents for a user for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent__user_id=user_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def user_monthly_spend(user_id: int) -> Decimal:
    """Sum spend across all agents for a user for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent__user_id=user_id,
        hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def compute_effective_spend_cap(
    *,
    session_spend_cap: Decimal | None,
    agent_daily_remaining: Decimal | None,
    agent_monthly_remaining: Decimal | None,
    user_daily_remaining: Decimal | None,
    user_monthly_remaining: Decimal | None,
) -> Decimal | None:
    """Return the tightest spend cap across all levels, or None if fully uncapped.

    Accepts negative "remaining" values (already over budget) — callers should
    treat any result <= 0 as immediately breached.
    """
    candidates = [
        v
        for v in (
            session_spend_cap,
            agent_daily_remaining,
            agent_monthly_remaining,
            user_daily_remaining,
            user_monthly_remaining,
        )
        if v is not None
    ]
    if not candidates:
        return None
    return min(candidates)
