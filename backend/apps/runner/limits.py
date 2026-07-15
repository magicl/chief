# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session limit checker — computes effective caps and runs pre-iteration checks.

Collapses the narrowing hierarchy + rolling budgets into two in-memory values
(effective_max_iterations and effective_spend_cap) that are compared every iteration
with zero DB queries. Long-running sessions refresh the spend cap periodically.
"""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID

from apps.runner.errors import (
    SessionIterationLimitExceeded,
    SessionSpendLimitExceeded,
)
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    compute_effective_spend_cap,
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

        # Compute full effective spend cap including rolling budgets
        self._last_budget_snapshot = time.monotonic()
        self.effective_spend_cap = self._compute_effective_spend_cap()

    def _compute_effective_spend_cap(self) -> Decimal | None:
        """Query HourlyUsage and compute the tightest spend cap across all levels."""
        agent_daily_remaining = None
        agent_monthly_remaining = None
        user_daily_remaining = None
        user_monthly_remaining = None

        if self._agent_id is not None and self._agent_daily_limit is not None:
            agent_daily_remaining = self._agent_daily_limit - agent_daily_spend(self._agent_id)
        if self._agent_id is not None and self._agent_monthly_limit is not None:
            agent_monthly_remaining = self._agent_monthly_limit - agent_monthly_spend(self._agent_id)
        if self._user_id is not None and self._user_daily_limit is not None:
            user_daily_remaining = self._user_daily_limit - user_daily_spend(self._user_id)
        if self._user_id is not None and self._user_monthly_limit is not None:
            user_monthly_remaining = self._user_monthly_limit - user_monthly_spend(self._user_id)

        return compute_effective_spend_cap(
            session_spend_cap=self._session_spend_cap,
            agent_daily_remaining=agent_daily_remaining,
            agent_monthly_remaining=agent_monthly_remaining,
            user_daily_remaining=user_daily_remaining,
            user_monthly_remaining=user_monthly_remaining,
        )

    def check(self) -> None:
        """Run all limit checks pre-iteration. Raises SessionFailure on breach."""
        self._maybe_refresh_spend_cap()

        if self.effective_max_iterations is not None and self.iteration_count >= self.effective_max_iterations:
            raise SessionIterationLimitExceeded(self.effective_max_iterations)

        if self.effective_spend_cap is not None and self.session_cost_usd >= self.effective_spend_cap:
            raise SessionSpendLimitExceeded(str(self.effective_spend_cap))

    def record_iteration(self) -> None:
        """Increment iteration count after a successful provider.collect()."""
        self.iteration_count += 1

    def record_cost(self, cost_usd: Decimal | None) -> None:
        """Accumulate spend after _emit_output."""
        if cost_usd is not None:
            self.session_cost_usd += cost_usd

    def _maybe_refresh_spend_cap(self) -> None:
        """Re-snapshot rolling budgets for long-running sessions (every 5 min)."""
        now = time.monotonic()
        if now - self._last_budget_snapshot < BUDGET_REFRESH_INTERVAL_S:
            return
        self.effective_spend_cap = self._compute_effective_spend_cap()
        self._last_budget_snapshot = now
