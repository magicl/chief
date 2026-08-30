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

from apps.agents.models import SpendPolicy
from apps.sessions.models import HourlyUsage
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone


def agent_daily_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for this agent for the current UTC day.

    Algorithm buckets carry a null agent_id, so they never count here.
    """
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def agent_monthly_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for this agent for the current UTC month.

    Algorithm buckets carry a null agent_id, so they never count here.
    """
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def user_daily_spend(user_id: int) -> Decimal:
    """Sum agent and algorithm HourlyUsage for this user for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def user_monthly_spend(user_id: int) -> Decimal:
    """Sum agent and algorithm HourlyUsage for this user for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))[
        'total'
    ] or Decimal(0)


def algorithm_daily_spend(user_id: int, algorithm_id: str) -> Decimal:
    """Sum this user's spend for one registry algorithm for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        algorithm_id=algorithm_id,
        hour__gte=today_start,
    ).aggregate(
        total=Sum('cost_usd')
    )['total'] or Decimal(0)


def algorithm_monthly_spend(user_id: int, algorithm_id: str) -> Decimal:
    """Sum this user's spend for one registry algorithm for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        algorithm_id=algorithm_id,
        hour__gte=month_start,
    ).aggregate(
        total=Sum('cost_usd')
    )['total'] or Decimal(0)


def resolve_user_spend_caps(user_id: int) -> tuple[Decimal | None, Decimal | None]:
    """Return the user's (daily, monthly) spend caps from SpendPolicy or global defaults.

    A per-user SpendPolicy value overrides the corresponding global default; a null
    column on an existing policy row leaves that level on the default.
    """
    daily_cap: Decimal | None = getattr(settings, 'DEFAULT_USER_DAILY_SPEND_LIMIT_USD', None)
    monthly_cap: Decimal | None = getattr(settings, 'DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD', None)
    try:
        policy = SpendPolicy.objects.get(user_id=user_id)
    except SpendPolicy.DoesNotExist:
        return daily_cap, monthly_cap
    if policy.daily_spend_limit_usd is not None:
        daily_cap = policy.daily_spend_limit_usd
    if policy.monthly_spend_limit_usd is not None:
        monthly_cap = policy.monthly_spend_limit_usd
    return daily_cap, monthly_cap


def user_rolling_cap_reached(user_id: int) -> bool:
    """Return True when a set user daily or monthly cap is met or exceeded.

    Spend covers every owner mode (agents and algorithms), so background
    algorithm work counts against the same user-level backstop.
    """
    daily_cap, monthly_cap = resolve_user_spend_caps(user_id)
    if daily_cap is not None and user_daily_spend(user_id) >= daily_cap:
        return True
    return monthly_cap is not None and user_monthly_spend(user_id) >= monthly_cap


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
