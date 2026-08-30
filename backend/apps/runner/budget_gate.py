# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pre-dispatch budget gate — checks whether an agent's budgets allow a new session."""

from __future__ import annotations

import logging

from apps.agents.models import Agent
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    user_rolling_cap_reached,
)
from django.conf import settings

logger = logging.getLogger(__name__)


def budget_allows_dispatch(agent: Agent) -> bool:
    """Return True when the agent's rolling budgets have remaining headroom.

    Checks (in order, short-circuiting on first breach):
    1. Agent daily spend vs per-agent cap (with global default fallback)
    2. Agent monthly spend vs per-agent cap (with global default fallback)
    3. User daily/monthly rolling caps, which also include algorithm-owned spend

    User-level cap resolution lives in ``apps.sessions.services.budget`` so the
    runner and background algorithms share one SpendPolicy read path.
    """
    agent_daily_cap = agent.daily_spend_limit_usd
    if agent_daily_cap is None:
        agent_daily_cap = getattr(settings, 'DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD', None)
    if agent_daily_cap is not None:
        if agent_daily_spend(agent.pk) >= agent_daily_cap:
            logger.info('Budget gate: agent %s exceeded daily spend cap', agent.pk)
            return False

    agent_monthly_cap = agent.monthly_spend_limit_usd
    if agent_monthly_cap is None:
        agent_monthly_cap = getattr(settings, 'DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD', None)
    if agent_monthly_cap is not None:
        if agent_monthly_spend(agent.pk) >= agent_monthly_cap:
            logger.info('Budget gate: agent %s exceeded monthly spend cap', agent.pk)
            return False

    if user_rolling_cap_reached(agent.user_id):
        logger.info('Budget gate: user %s reached a rolling spend cap', agent.user_id)
        return False

    return True
