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
3. **Per-agent rolling spend budget** — limit total spend across all sessions for an agent (daily/monthly).
4. **Per-user global spend backstop** — a user-level ceiling that caps total spend across all agents (daily/monthly).

Limits at each level can only **narrow** (reduce) the effective limit — never loosen it.

### Non-goals

- Billing/invoicing system.
- External webhook/notification on breach (future work).
- Per-tool-call cost budgets (too granular for v1).
- UI for configuring user-level limits (admin-only/settings for now).
- Rolling iteration budgets at agent/user level (iteration cost varies by model; spend is the meaningful cross-agent metric).

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

### Rolling spend budgets

| Level | Scope | Windows | Metric |
|-------|-------|---------|--------|
| Agent | All sessions for one agent | daily, monthly | Spend (USD) only |
| User  | All sessions across all agents for one user | daily, monthly | Spend (USD) only |

No iteration tracking at rolling level — iteration cost varies by model/context, making cross-session iteration counts meaningless for budget purposes.

### Enforcement points

Two enforcement sites only:

| Site | Where | Checks run |
|------|-------|------------|
| **Pre-iteration** | Top of `SessionRunner.run()` while-loop, before `provider.collect()` | All: session iteration cap, session spend cap, agent rolling spend, user rolling spend |
| **Pre-scheduling** | `scheduling.py` before dispatch | Subset: agent rolling spend, user rolling spend |

This keeps enforcement logic consolidated — the loop has one checkpoint that runs everything, and the scheduler runs the budget subset to avoid creating sessions that will immediately fail.

### Failure behavior

On any limit breach:
1. Emit a `FAILURE` event with a descriptive code.
2. Set session status to `WAITING` (preserves state for inspection and potential resume after budget adjustment).
3. Log the breach at WARNING level.

Failure codes:
- `session_iteration_limit` — per-session iteration cap exceeded
- `session_spend_limit` — per-session spend cap exceeded
- `agent_daily_spend_limit` — agent rolling daily spend budget
- `agent_monthly_spend_limit` — agent rolling monthly spend budget
- `user_daily_spend_limit` — user backstop daily spend
- `user_monthly_spend_limit` — user backstop monthly spend

---

## Hourly Usage Aggregation

### Problem

Checking rolling budgets by summing raw `AgentSessionEvent.cost_usd` rows is too expensive to run every loop iteration — potentially millions of events per agent per month.

### Solution: `HourlyUsage` table + periodic celery aggregation

A pre-aggregated table with one row per (agent, model, hour). A celery task runs every ~10 minutes to roll up recent events into this table.

Budget checks then `SUM` over `HourlyUsage` rows — at most 24 rows/day or ~720/month per agent×model combination. Fast and bounded.

### `HourlyUsage` model (`apps/sessions/models.py`)

```python
class HourlyUsage(models.Model):
    """Pre-aggregated token and spend totals per agent per model per hour.

    Populated by a periodic celery task that rolls up AgentSessionEvent rows.
    Consumed by budget-check queries (daily/monthly spend sums).
    """
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='hourly_usage')
    hour = models.DateTimeField()  # truncated to hour boundary
    model = models.CharField(max_length=255)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    cache_creation_input_tokens = models.PositiveBigIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    iteration_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['agent', 'hour', 'model'],
                name='sessions_hourlyusage_agent_hour_model_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['agent', 'hour']),
        ]
```

### Celery aggregation task

Runs every ~10 minutes via celery beat:

1. Find `AgentSessionEvent` rows with `kind=OUTPUT` that are newer than the last aggregation watermark (or a lookback window, e.g. last 2 hours for safety).
2. Group by `(session__agent_id, model, hour_trunc(created_at))`.
3. For each group, upsert into `HourlyUsage` using `update_or_create` with `F()` increments (idempotent if events are re-processed — use a high-water-mark or deduplicate by tracking `last_aggregated_event_id`).

Watermark tracking: store the last processed `AgentSessionEvent.created_at` (or event id) in a small singleton model or Django cache, to avoid reprocessing the entire table each run.

### Budget check query

```python
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta

def agent_daily_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)

def user_daily_spend(user_id: int) -> Decimal:
    """Sum spend across all agents for a user for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent__user_id=user_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)
```

Monthly variants filter `hour__gte` to the first of the current month.

### Staleness window

The aggregation task runs every 10 minutes, so budget data may be up to ~10 minutes stale. This is acceptable because:
- Per-session limits (iteration + spend) are enforced in real-time from in-memory accumulators.
- Rolling budgets are a slower-moving safety net — 10-minute lag before catching a runaway is fine for daily/monthly windows.
- A session that's actively burning money is still constrained by its per-session spend cap.

---

## Data Model Changes

### AgentConfigSpec (Pydantic — `libs/agent_spec/spec.py`)

New optional `limits` block:

```python
class SessionLimitsSpec(BaseModel):
    """Per-session hard limits declared in agent config YAML."""
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None

class AgentConfigSpec(BaseModel):
    # ... existing fields ...
    limits: SessionLimitsSpec = Field(default_factory=SessionLimitsSpec)
```

### TriggerSpec (Pydantic — `libs/agent_spec/spec.py`)

Add optional limit fields (can only narrow agent-level):

```python
class TriggerSpec(BaseModel):
    # ... existing fields ...
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None
```

### Agent model (Django — `apps/agents/models.py`)

New fields for rolling spend budgets:

```python
class Agent(models.Model):
    # ... existing fields ...
    daily_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    monthly_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
```

### User-level limits (Django — `apps/agents/models.py`)

A `SpendPolicy` model:

```python
class SpendPolicy(models.Model):
    """Per-user global spend backstop (daily + monthly)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spend_policy',
    )
    daily_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    monthly_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
```

### Django settings (global defaults)

```python
# Per-session defaults (narrowing hierarchy floor)
DEFAULT_MAX_SESSION_ITERATIONS: int | None = 200
DEFAULT_MAX_SESSION_COST_USD: Decimal | None = Decimal("5.00")

# Agent rolling spend defaults (None = no global default; agent field overrides)
DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None

# User rolling spend defaults (None = no global default; SpendPolicy overrides)
DEFAULT_USER_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None
```

---

## Session Loop Changes (`apps/runner/loop.py`)

The `SessionRunner` gains a single pre-iteration checkpoint:

```python
# At the top of the while-loop body, before provider.collect():
self._check_limits()
```

`_check_limits()` runs all checks in order:
1. Session iteration count >= effective `max_iterations` → fail
2. Session accumulated spend >= effective `max_cost_usd` → fail
3. Agent daily/monthly spend (from `HourlyUsage` + current session's local accumulator) >= budget → fail
4. User daily/monthly spend (same approach) >= budget → fail

The method raises a `SessionFailure` (already handled by the loop) on breach.

### In-memory state

```python
self._iteration_count: int = 0
self._session_cost_usd: Decimal = Decimal(0)
```

- `_iteration_count` incremented after each successful `provider.collect()`.
- `_session_cost_usd` updated after each `_emit_output()` (where cost is computed).

For rolling budget checks, the session queries `HourlyUsage` once at start (to get the baseline) and adds its own `_session_cost_usd` on top for comparison. Re-queries every ~5 minutes or N iterations to pick up aggregation updates from other concurrent sessions.

---

## Scheduling Gate (`apps/runner/scheduling.py`)

Before dispatching a new session:

1. Check agent rolling spend budget (daily + monthly) via `HourlyUsage` sum.
2. Check user global spend backstop (daily + monthly) via `HourlyUsage` sum.
3. If either is exceeded, skip dispatch (log at WARNING, do not create a session).

---

## YAML Surface

Example agent config with limits:

```yaml
schema_version: 4
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

Agent-level and user-level rolling budgets are configured via Django model fields (admin/API), not in YAML — they apply across all configs and sessions for that agent/user.

---

## Testing Strategy

- Unit tests for `SessionLimitsSpec` validation (negative values rejected, None allowed).
- Unit tests for effective limit computation (`min` across levels with None handling).
- Unit tests for `HourlyUsage` aggregation task (correct grouping, idempotent re-runs).
- Integration tests with `SessionRunner` using a mock/fake provider — verify session halts at configured iteration/cost limit.
- Integration tests for rolling budget enforcement (populate `HourlyUsage`, verify session halts and scheduler skips dispatch).
- Integration tests for the scheduling gate (budget exceeded → no dispatch).

---

## Migration Path

1. Add `SessionLimitsSpec` to the spec schema + trigger fields (bump `AGENT_CONFIG_SPEC_VERSION` to 4 with a spec migration that defaults missing `limits` to empty).
2. Django migration: `Agent.daily_spend_limit_usd`, `Agent.monthly_spend_limit_usd` (nullable).
3. Django migration: `SpendPolicy` model.
4. Django migration: `HourlyUsage` model.
5. Celery beat task for hourly aggregation.
6. Loop enforcement (`_check_limits` in `SessionRunner`).
7. Scheduling gate in `scheduling.py`.

---

## Open Questions

| # | Question | Proposed answer |
|---|----------|-----------------|
| 1 | Global defaults: what's a sane iteration default? | 200 iterations (covers most useful agent runs; prevents infinite loops). |
| 2 | Global defaults: what's a sane cost default? | $5.00 per session. |
| 3 | Should `max_cost_usd` in YAML be a string or number? | Number in YAML (e.g. `2.00`), parsed to `Decimal` in Pydantic via a validator. |
| 4 | Resume after limit breach — should it reset counters? | No — counters persist. User must raise the limit or wait for the budget window to roll over. |
| 5 | Aggregation watermark storage? | Django cache key or a small `AggregationState` singleton model. Prefer model for durability across restarts. |
| 6 | Should the current session's unaggregated spend be added to the `HourlyUsage` sum for budget checks? | Yes — `_session_cost_usd` is added to the queried baseline to avoid the 10-min blind spot for the running session's own spend. |
