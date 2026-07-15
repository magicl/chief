# Session Limits — Design

**Branch:** `feat/2026-07-15-session-limits`

Status: **design**

**ClickUp:** https://app.clickup.com/t/868kcrc7c (iteration limits), https://app.clickup.com/t/868kcrcf9 (spend limits)
**ClickUp branch field:** `feat/2026-07-15-session-limits`

Follow the `clickup` skill for status/tag/Branch updates.

---

## Goal

Prevent cost overruns by enforcing hard limits on agent sessions at multiple levels:

1. **Per-session iteration cap** — limit how many LLM calls a single session can make.
2. **Per-session spend cap** — limit how many USD a single session can accumulate.
3. **Per-agent rolling budget** — limit total iterations and spend across all sessions for an agent over a rolling window.
4. **Per-user global backstop** — a user-level ceiling that caps total spend/iterations across all of a user's agents.

Limits at each level can only **narrow** (reduce) the effective limit — never loosen it.

### Non-goals

- Billing/invoicing system.
- External webhook/notification on breach (future work).
- Per-tool-call cost budgets (too granular for v1).
- UI for configuring user-level limits (admin-only/settings for now).

---

## Architecture

### Narrowing hierarchy (per-session limits)

```
effective_max_iterations = min(
    settings.DEFAULT_MAX_SESSION_ITERATIONS,   # global floor
    spec.limits.max_iterations,                # agent-level
    trigger.max_iterations,                    # trigger-level
)

effective_max_cost_usd = min(
    settings.DEFAULT_MAX_SESSION_COST_USD,     # global floor
    spec.limits.max_cost_usd,                  # agent-level
    trigger.max_cost_usd,                      # trigger-level
)
```

Each level is optional (None = no cap at that level). `min()` ignores None values. If all are None, the session is uncapped (but rolling budgets still apply).

### Rolling budgets

| Level | Scope | Windows |
|-------|-------|---------|
| Agent | All sessions for one agent | daily, monthly |
| User  | All sessions across all agents for one user | daily, monthly |

Rolling budgets track both **iterations** (count of LLM generate calls) and **spend** (sum of `cost_usd` from session events).

### Enforcement points

| Check | Where | When |
|-------|-------|------|
| Session iteration cap | `SessionRunner.run()` loop | After each `provider.collect()` call |
| Session spend cap | `SessionRunner.run()` loop | After each `_emit_output()` (cost computed) |
| Agent rolling budget | `scheduling.py` dispatch gate + `SessionRunner` pre-generate | Before starting a session; before each LLM call |
| User global backstop | `scheduling.py` dispatch gate + `SessionRunner` pre-generate | Same as agent budget |

### Failure behavior

On any limit breach:
1. Emit a `FAILURE` event with a descriptive code.
2. Set session status to `WAITING` (preserves state for inspection and potential resume after budget adjustment).
3. Log the breach at WARNING level.

Failure codes:
- `session_iteration_limit` — per-session iteration cap exceeded
- `session_spend_limit` — per-session spend cap exceeded
- `agent_daily_iteration_limit` — agent rolling daily iteration budget
- `agent_daily_spend_limit` — agent rolling daily spend budget
- `agent_monthly_iteration_limit` — agent rolling monthly iteration budget
- `agent_monthly_spend_limit` — agent rolling monthly spend budget
- `user_daily_iteration_limit` — user backstop daily iterations
- `user_daily_spend_limit` — user backstop daily spend
- `user_monthly_iteration_limit` — user backstop monthly iterations
- `user_monthly_spend_limit` — user backstop monthly spend

---

## Data Model Changes

### AgentConfigSpec (Pydantic — `libs/agent_spec/spec.py`)

New optional `limits` block:

```python
class SessionLimitsSpec(BaseModel):
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None

class AgentConfigSpec(BaseModel):
    # ... existing fields ...
    limits: SessionLimitsSpec = SessionLimitsSpec()
```

### TriggerSpec (Pydantic — `libs/agent_spec/spec.py`)

Add optional limit fields:

```python
class TriggerSpec(BaseModel):
    # ... existing fields ...
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None
```

### Agent model (Django — `apps/agents/models.py`)

New fields for rolling budgets:

```python
class Agent(models.Model):
    # ... existing fields ...
    daily_iteration_limit: int | None
    daily_spend_limit_usd: Decimal | None
    monthly_iteration_limit: int | None
    monthly_spend_limit_usd: Decimal | None
```

### User-level limits (Django — new model or on User)

A `SpendPolicy` model (or fields on the User model — TBD based on auth setup):

```python
class SpendPolicy(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    daily_iteration_limit: int | None
    daily_spend_limit_usd: Decimal | None
    monthly_iteration_limit: int | None
    monthly_spend_limit_usd: Decimal | None
```

### Django settings (global defaults)

```python
DEFAULT_MAX_SESSION_ITERATIONS: int | None = 200
DEFAULT_MAX_SESSION_COST_USD: Decimal | None = Decimal("5.00")
DEFAULT_AGENT_DAILY_ITERATION_LIMIT: int | None = None
DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_AGENT_MONTHLY_ITERATION_LIMIT: int | None = None
DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_USER_DAILY_ITERATION_LIMIT: int | None = None
DEFAULT_USER_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_USER_MONTHLY_ITERATION_LIMIT: int | None = None
DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None
```

---

## Session Loop Changes (`apps/runner/loop.py`)

The `SessionRunner.run()` while-loop gains:

1. **Iteration counter** — incremented after each successful `provider.collect()`.
2. **Cost accumulator** — `Decimal` summing `cost_usd` from each output event.
3. **Pre-generate budget check** — before calling `provider.collect()`, query rolling budgets (agent + user). This is an aggregate query but only needs to run once per session start and then can be maintained via the local accumulator (the session's own contribution is tracked in-memory; other sessions' contributions are checked at start and periodically — e.g. every N iterations or every 60s).
4. **Post-generate limit check** — after `_emit_output()`, compare iteration count and accumulated cost against effective limits.

### Budget query optimization

Full aggregate queries (`SUM(cost_usd)` over recent events) are expensive per-iteration. Strategy:

- At session start: query current daily/monthly totals for the agent and user. Cache as `budget_baseline`.
- In-loop: add local session cost to baseline for comparison. Only re-query the DB every ~10 iterations or 60 seconds (stale-but-safe — other sessions running concurrently could push over budget, but the periodic refresh catches it without per-call DB hits).
- On breach: halt immediately.

---

## Scheduling Gate (`apps/runner/scheduling.py`)

Before dispatching a new session:

1. Check agent rolling budget (daily + monthly iterations and spend).
2. Check user global backstop.
3. If either is exceeded, skip dispatch (log at WARNING, do not create a session).

---

## YAML Surface

Example agent config with limits:

```yaml
schema_version: 3
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
system_prompt: "..."
limits:
  max_iterations: 50
  max_cost_usd: 2.00
triggers:
  - name: scheduled-cleanup
    kind: schedule
    cron: "0 */6 * * *"
    prompt: "Check inbox and triage"
    max_iterations: 20
    max_cost_usd: 0.50
```

---

## Testing Strategy

- Unit tests for `SessionLimitsSpec` validation (negative values rejected, None allowed).
- Unit tests for effective limit computation (`min` across levels with None handling).
- Integration tests with `SessionRunner` using a mock provider that returns N responses — verify the session halts at the configured iteration/cost limit.
- Integration tests for the scheduling gate (budget exceeded → no dispatch).
- Tests for rolling budget queries (sum over time windows).

---

## Migration Path

1. Add `SessionLimitsSpec` + trigger fields to the spec schema (bump `AGENT_CONFIG_SPEC_VERSION` to 4 with a migration that defaults missing `limits` to empty).
2. Django migration for `Agent` budget fields (nullable, so backwards-compatible).
3. Django migration for `SpendPolicy` model.
4. Loop enforcement (no schema dependency — purely runtime).
5. Scheduling gate.

---

## Open Questions

| # | Question | Proposed answer |
|---|----------|-----------------|
| 1 | Should breaching a rolling budget kill already-running sessions? | Yes — check in the loop; halt gracefully. |
| 2 | Global defaults: what's a sane iteration default? | 200 iterations (covers most useful agent runs; prevents infinite loops). |
| 3 | Global defaults: what's a sane cost default? | $5.00 per session (enough for complex multi-tool runs). |
| 4 | Should we support `max_cost_usd` at trigger level as a string or Decimal in YAML? | String in YAML, parsed to `Decimal` in Pydantic. |
| 5 | Resume after limit breach — should it reset counters? | No — counters persist across resume. The user must raise the limit or the budget must recover (next day/month). |
