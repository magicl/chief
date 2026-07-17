# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session limit checker — computes effective caps and runs pre-iteration checks.

Collapses the narrowing hierarchy (global settings > agent spec > trigger spec)
into in-memory limits.  Rolling budgets (agent daily/monthly, user daily/monthly)
are tracked per-level so check() can raise the specific failure class for whichever
level is breached.  Long-running sessions refresh rolling budgets periodically.
"""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID

from apps.runner.errors import (
    AgentDailySpendLimitExceeded,
    AgentMonthlySpendLimitExceeded,
    SessionIterationLimitExceeded,
    SessionSpendLimitExceeded,
    UserDailySpendLimitExceeded,
    UserMonthlySpendLimitExceeded,
)
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    user_daily_spend,
    user_monthly_spend,
)
from django.conf import settings
from libs.agent_spec import AgentConfigSpec, TriggerSpec

BUDGET_REFRESH_INTERVAL_S = 300  # 5 minutes


def _min_non_none_int(*values: int | None) -> int | None:
    """Return the smallest non-None int, or None if all are None."""
    candidates = [v for v in values if v is not None]
    return min(candidates) if candidates else None


def _min_non_none_decimal(*values: Decimal | None) -> Decimal | None:
    """Return the smallest non-None Decimal, or None if all are None."""
    candidates = [v for v in values if v is not None]
    return min(candidates) if candidates else None


class SessionLimitChecker:
    """Tracks iteration count and spend, enforces limits in-memory.

    Computes effective caps at init from the narrowing hierarchy
    (global settings > agent spec > trigger spec) plus rolling budgets.
    The check() method is called pre-iteration — pure in-memory comparisons.

    Rolling budget remaining amounts are stored per-level so check() can raise
    the specific failure class for the breached level.
    """

    def __init__(
        self,
        spec: AgentConfigSpec,
        *,
        trigger_spec: TriggerSpec | None = None,
        agent_id: UUID | None = None,
        user_id: int | None = None,
        agent_daily_limit: Decimal | None = None,
        agent_monthly_limit: Decimal | None = None,
        user_daily_limit: Decimal | None = None,
        user_monthly_limit: Decimal | None = None,
    ) -> None:
        """Initialize limit checker with the full narrowing hierarchy context."""
        self._agent_id = agent_id
        self._user_id = user_id
        self._agent_daily_limit = agent_daily_limit
        self._agent_monthly_limit = agent_monthly_limit
        self._user_daily_limit = user_daily_limit
        self._user_monthly_limit = user_monthly_limit

        self.iteration_count: int = 0
        self.session_cost_usd: Decimal = Decimal(0)

        # Compute effective iteration cap (narrowing: global > agent > trigger)
        trigger_max_iter = trigger_spec.max_iterations if trigger_spec else None
        self.effective_max_iterations = _min_non_none_int(
            getattr(settings, 'DEFAULT_MAX_SESSION_ITERATIONS', None),
            spec.limits.max_iterations,
            trigger_max_iter,
        )

        # Compute session-level spend cap from narrowing hierarchy
        trigger_max_cost = trigger_spec.max_cost_usd if trigger_spec else None
        self._session_spend_cap = _min_non_none_decimal(
            getattr(settings, 'DEFAULT_MAX_SESSION_COST_USD', None),
            spec.limits.max_cost_usd,
            trigger_max_cost,
        )

        # Per-level remaining amounts (refreshed together)
        self._agent_daily_remaining: Decimal | None = None
        self._agent_monthly_remaining: Decimal | None = None
        self._user_daily_remaining: Decimal | None = None
        self._user_monthly_remaining: Decimal | None = None

        self._last_budget_snapshot = time.monotonic()
        self._refresh_budget_levels()

    def _refresh_budget_levels(self) -> None:
        """Query HourlyUsage and store per-level remaining budget for this session.

        Subtracts ``session_cost_usd`` from the aggregated baseline to avoid
        double-counting after the aggregation task folds this session's events
        into HourlyUsage.
        """
        if self._agent_id is not None and self._agent_daily_limit is not None:
            baseline = agent_daily_spend(self._agent_id)
            others = max(baseline - self.session_cost_usd, Decimal(0))
            self._agent_daily_remaining = self._agent_daily_limit - others
        else:
            self._agent_daily_remaining = None

        if self._agent_id is not None and self._agent_monthly_limit is not None:
            baseline = agent_monthly_spend(self._agent_id)
            others = max(baseline - self.session_cost_usd, Decimal(0))
            self._agent_monthly_remaining = self._agent_monthly_limit - others
        else:
            self._agent_monthly_remaining = None

        if self._user_id is not None and self._user_daily_limit is not None:
            baseline = user_daily_spend(self._user_id)
            others = max(baseline - self.session_cost_usd, Decimal(0))
            self._user_daily_remaining = self._user_daily_limit - others
        else:
            self._user_daily_remaining = None

        if self._user_id is not None and self._user_monthly_limit is not None:
            baseline = user_monthly_spend(self._user_id)
            others = max(baseline - self.session_cost_usd, Decimal(0))
            self._user_monthly_remaining = self._user_monthly_limit - others
        else:
            self._user_monthly_remaining = None

    def check(self) -> None:
        """Run all limit checks pre-iteration. Raises the most specific SessionFailure."""
        self._maybe_refresh_budget()

        if self.effective_max_iterations is not None and self.iteration_count >= self.effective_max_iterations:
            raise SessionIterationLimitExceeded(self.effective_max_iterations)

        if self._agent_daily_remaining is not None and self.session_cost_usd >= self._agent_daily_remaining:
            raise AgentDailySpendLimitExceeded(str(self._agent_daily_limit))

        if self._agent_monthly_remaining is not None and self.session_cost_usd >= self._agent_monthly_remaining:
            raise AgentMonthlySpendLimitExceeded(str(self._agent_monthly_limit))

        if self._user_daily_remaining is not None and self.session_cost_usd >= self._user_daily_remaining:
            raise UserDailySpendLimitExceeded(str(self._user_daily_limit))

        if self._user_monthly_remaining is not None and self.session_cost_usd >= self._user_monthly_remaining:
            raise UserMonthlySpendLimitExceeded(str(self._user_monthly_limit))

        if self._session_spend_cap is not None and self.session_cost_usd >= self._session_spend_cap:
            raise SessionSpendLimitExceeded(str(self._session_spend_cap))

    def record_iteration(self) -> None:
        """Increment iteration count after a successful provider.collect()."""
        self.iteration_count += 1

    def record_cost(self, cost_usd: Decimal | None) -> None:
        """Accumulate spend after _emit_output."""
        if cost_usd is not None:
            self.session_cost_usd += cost_usd

    def _maybe_refresh_budget(self) -> None:
        """Re-snapshot rolling budgets for long-running sessions (every 5 min)."""
        now = time.monotonic()
        if now - self._last_budget_snapshot < BUDGET_REFRESH_INTERVAL_S:
            return
        self._refresh_budget_levels()
        self._last_budget_snapshot = now
