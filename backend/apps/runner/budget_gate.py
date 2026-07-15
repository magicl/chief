# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pre-dispatch budget gate — checks whether an agent's budgets allow a new session."""

from __future__ import annotations

import logging
from decimal import Decimal

from apps.agents.models import Agent, SpendPolicy
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    user_daily_spend,
    user_monthly_spend,
)
from django.conf import settings

logger = logging.getLogger(__name__)


def budget_allows_dispatch(agent: Agent) -> bool:
    """Return True when the agent's rolling budgets have remaining headroom.

    Checks (in order, short-circuiting on first breach):
    1. Agent daily spend vs agent.daily_spend_limit_usd
    2. Agent monthly spend vs agent.monthly_spend_limit_usd
    3. User daily spend vs SpendPolicy.daily_spend_limit_usd (or global default)
    4. User monthly spend vs SpendPolicy.monthly_spend_limit_usd (or global default)
    """
    agent_daily_cap = agent.daily_spend_limit_usd
    if agent_daily_cap is not None:
        if agent_daily_spend(agent.pk) >= agent_daily_cap:
            logger.info('Budget gate: agent %s exceeded daily spend cap', agent.pk)
            return False

    agent_monthly_cap = agent.monthly_spend_limit_usd
    if agent_monthly_cap is not None:
        if agent_monthly_spend(agent.pk) >= agent_monthly_cap:
            logger.info('Budget gate: agent %s exceeded monthly spend cap', agent.pk)
            return False

    user_daily_cap = _user_daily_cap(agent.user_id)
    if user_daily_cap is not None:
        if user_daily_spend(agent.user_id) >= user_daily_cap:
            logger.info('Budget gate: user %s exceeded daily spend cap', agent.user_id)
            return False

    user_monthly_cap = _user_monthly_cap(agent.user_id)
    if user_monthly_cap is not None:
        if user_monthly_spend(agent.user_id) >= user_monthly_cap:
            logger.info('Budget gate: user %s exceeded monthly spend cap', agent.user_id)
            return False

    return True


def _user_daily_cap(user_id: int) -> Decimal | None:
    """Resolve user daily spend cap from SpendPolicy or global default."""
    try:
        policy = SpendPolicy.objects.get(user_id=user_id)
        if policy.daily_spend_limit_usd is not None:
            return policy.daily_spend_limit_usd
    except SpendPolicy.DoesNotExist:
        pass
    return getattr(settings, 'DEFAULT_USER_DAILY_SPEND_LIMIT_USD', None)


def _user_monthly_cap(user_id: int) -> Decimal | None:
    """Resolve user monthly spend cap from SpendPolicy or global default."""
    try:
        policy = SpendPolicy.objects.get(user_id=user_id)
        if policy.monthly_spend_limit_usd is not None:
            return policy.monthly_spend_limit_usd
    except SpendPolicy.DoesNotExist:
        pass
    return getattr(settings, 'DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD', None)
