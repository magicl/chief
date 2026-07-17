# Session Limits — Code Review

**Spec:** `docs/specs/2026-07-15-session-limits/`
**Branch:** `feat/2026-07-15-session-limits`
**Range:** `2075ea64..f1d92021`
**Reviewed:** 2026-07-15

## Strengths

- Clean layered architecture — narrowing hierarchy, budget gate, and loop enforcement well-separated
- Idempotent aggregation task with 2-hour lookback (justified simplification over watermark)
- Comprehensive spec-layer tests (validation, defaults, migration chain integrity)
- Zero per-iteration DB queries — `check()` is pure in-memory comparison
- Budget gate tests cover all 4 dimensions + exact-limit boundary

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| C1 | Fixed | `loop.py:85-94` (`for_session`) | `for_session` never passes `agent_id`, agent/user limits, or `SpendPolicy` to `SessionLimitChecker` — rolling budgets are entirely bypassed in production | Wired agent_id, agent limits, user limits (SpendPolicy + fallback), and trigger_spec through `for_session` |
| C2 | Fixed | `limits.py:98-120` (`_compute_effective_spend_cap`) | On 5-min refresh, `HourlyUsage` sum may include this session's spend (once aggregation catches up), but `session_cost_usd` still carries the same amount — double-count prematurely terminates sessions | `_refresh_budget_levels` now subtracts `session_cost_usd` from baseline before computing remaining |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| I1 | Fixed | `loop.py:79-83` | `trigger_spec` never resolved or passed — trigger-level `max_iterations`/`max_cost_usd` silently ignored in production | `for_session` resolves trigger from `session.trigger_ref` and passes `TriggerSpec` to checker |
| I2 | Fixed | `errors.py:42-55` | Only 2 of 6 designed failure classes implemented — all rolling budget breaches report as `session_spend_limit` with no way to distinguish which level triggered | Added 4 classes: `AgentDaily/MonthlySpendLimitExceeded`, `UserDaily/MonthlySpendLimitExceeded` |
| I3 | Fixed | `views.py` / templates | No view/template tests for dashboard and agent detail usage context | Created `backend/apps/web/tests/test_usage_views.py` with 4 tests |
| I4 | Fixed | `budget_gate.py:33-36` | Agent-level budget gate doesn't fall back to `DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD` / `DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD` settings — inconsistent with user-level fallback pattern | Added settings fallback matching user-level pattern |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| M1 | Fixed | `tasks.py:107-121` | Tool call count written identically to every model row for same (agent, hour) — inflates totals when summed across models | Now writes full count only on first model row per (agent, hour), 0 for others |
| M2 | Fixed | `budget_gate.py` (`_user_daily_cap` / `_user_monthly_cap`) | Two separate `SpendPolicy.objects.get()` queries per dispatch | Combined into single `_resolve_user_caps` function |
| M3 | Fixed | `scheduling.py:133-136` | `budget_allows_dispatch` runs inside `select_for_update` atomic block — 4 queries while holding trigger lock | Moved before atomic block in both dispatch paths |

## Assessment

**Ready to merge:** No

**Reasoning:** C1 (rolling budgets completely bypassed in production) and C2 (double-count bug on refresh) mean the feature's core value proposition doesn't work in the celery runner path. The scheduling gate provides dispatch-time protection only.
