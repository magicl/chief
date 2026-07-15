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
| C1 | | `loop.py:85-94` (`for_session`) | `for_session` never passes `agent_id`, agent/user limits, or `SpendPolicy` to `SessionLimitChecker` — rolling budgets are entirely bypassed in production | Only scheduling gate provides protection; running sessions have no rolling budget awareness |
| C2 | | `limits.py:98-120` (`_compute_effective_spend_cap`) | On 5-min refresh, `HourlyUsage` sum may include this session's spend (once aggregation catches up), but `session_cost_usd` still carries the same amount — double-count prematurely terminates sessions | Design Open Question #6 addressed this but implementation doesn't account for it |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| I1 | | `loop.py:79-83` | `trigger_spec` never resolved or passed — trigger-level `max_iterations`/`max_cost_usd` silently ignored in production | Users can configure trigger limits in YAML but they do nothing |
| I2 | | `errors.py:42-55` | Only 2 of 6 designed failure classes implemented — all rolling budget breaches report as `session_spend_limit` with no way to distinguish which level triggered | UX and debugging impact |
| I3 | | `views.py` / templates | No view/template tests for dashboard and agent detail usage context | Plan Task 8 specified these |
| I4 | | `budget_gate.py:33-36` | Agent-level budget gate doesn't fall back to `DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD` / `DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD` settings — inconsistent with user-level fallback pattern | |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| M1 | | `tasks.py:107-121` | Tool call count written identically to every model row for same (agent, hour) — inflates totals when summed across models | Not used for budget decisions currently |
| M2 | | `budget_gate.py` (`_user_daily_cap` / `_user_monthly_cap`) | Two separate `SpendPolicy.objects.get()` queries per dispatch | Minor perf; combine into one lookup |
| M3 | | `scheduling.py:133-136` | `budget_allows_dispatch` runs inside `select_for_update` atomic block — 4 queries while holding trigger lock | Move before atomic as pre-filter; bounded overshoot is acceptable |

## Assessment

**Ready to merge:** No

**Reasoning:** C1 (rolling budgets completely bypassed in production) and C2 (double-count bug on refresh) mean the feature's core value proposition doesn't work in the celery runner path. The scheduling gate provides dispatch-time protection only.
