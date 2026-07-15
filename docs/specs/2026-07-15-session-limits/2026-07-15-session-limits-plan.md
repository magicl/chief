# Session Limits — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. `/impl` creates or checks out the declared feature branch before the first code change. Then create `docs/specs/2026-07-15-session-limits/2026-07-15-session-limits-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Enforce hard session limits (iteration + spend) and rolling spend budgets (agent + user) to prevent cost overruns, backed by an efficient hourly usage aggregation table.

**Architecture:** Per-session limits use a narrowing hierarchy (global → agent spec → trigger); rolling budgets are checked via a pre-aggregated `HourlyUsage` table populated by a periodic celery task. The loop computes effective caps at session start and compares in-memory — zero per-iteration DB queries.

**Tech Stack:** Django 5.x, Pydantic v2, Celery, PostgreSQL

**Branch:** `feat/2026-07-15-session-limits`

**ClickUp:** https://app.clickup.com/t/868kcrc7c, https://app.clickup.com/t/868kcrcf9

| Stage | ClickUp action |
|-------|----------------|
| Already done at design start | Status `doing`, tag `agent`, Branch field set |
| Implementation complete + verification green + PR open | Status `review`; comment with PR URL |

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (or scoped tests while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-07-15-session-limits` from the plan, and after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`ai/commands/py-checks.md`)
- **CLI stdout:** capture with `self.captureStdout()` and assert; do not leave `click.echo` status lines uncaptured (`ai/commands/py-checks.md`)
- **Final task:** code review via **`superpowers/requesting-code-review`** (see mandatory **S_final** section below)
- Test naming: avoid keywords `error`, `exception`, `warning`, `deprecated` in test names

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `backend/libs/agent_spec/spec.py` | Modify | Add `SessionLimitsSpec`, update `AgentConfigSpec` + `TriggerSpec` |
| `backend/libs/agent_spec/migrations/004_session_limits.py` | Create | Spec migration v3→v4 |
| `backend/libs/agent_spec/tests/test_examples.py` | Modify | Update to schema v4 assertions |
| `backend/apps/agents/models.py` | Modify | Add spend limit fields to `Agent`; add `SpendPolicy` model |
| `backend/apps/sessions/models.py` | Modify | Add `HourlyUsage` model |
| `backend/apps/sessions/tasks.py` | Modify | Add `aggregate_hourly_usage` celery task |
| `backend/apps/sessions/services/budget.py` | Create | Budget query helpers + effective cap computation |
| `backend/apps/runner/limits.py` | Create | `SessionLimitChecker` — computes effective caps, runs checks |
| `backend/apps/runner/loop.py` | Modify | Wire `SessionLimitChecker` into the while-loop |
| `backend/apps/runner/scheduling.py` | Modify | Add budget gate before dispatch |
| `backend/apps/runner/errors.py` | Modify | Add limit-breach `SessionFailure` subclasses |
| `backend/chief/celery.py` | Modify | Register aggregation beat schedule |
| `backend/chief/tasks.py` | Modify | Import sessions tasks |
| `backend/chief/settings.py` | Modify | Add default limit settings |
| `backend/apps/runner/tests/test_limits.py` | Create | Unit + integration tests for limit enforcement |
| `backend/apps/sessions/tests/test_aggregation.py` | Create | Tests for the hourly aggregation task |
| `backend/apps/runner/tests/test_scheduling.py` | Modify | Add budget gate tests |

---

## Task 1: Spec schema — `SessionLimitsSpec` and trigger fields

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Create: `backend/libs/agent_spec/migrations/004_session_limits.py`
- Modify: `backend/libs/agent_spec/__init__.py` (export new types)
- Test: `backend/apps/agents/tests/test_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# In backend/apps/agents/tests/test_spec.py — add to existing test class

def test_session_limits_spec_defaults_to_uncapped(self) -> None:
    spec = AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
    )
    self.assertIsNone(spec.limits.max_iterations)
    self.assertIsNone(spec.limits.max_cost_usd)

def test_session_limits_spec_accepts_valid_values(self) -> None:
    spec = AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        limits={'max_iterations': 50, 'max_cost_usd': '2.00'},
    )
    self.assertEqual(spec.limits.max_iterations, 50)
    self.assertEqual(spec.limits.max_cost_usd, Decimal('2.00'))

def test_session_limits_rejects_negative_iterations(self) -> None:
    with self.assertRaises(ValidationError):
        AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits={'max_iterations': -1},
        )

def test_trigger_spec_accepts_limit_fields(self) -> None:
    trigger = TriggerSpec(
        name='sweep', kind='schedule', cron='0 * * * *',
        prompt='go', max_iterations=20, max_cost_usd='0.50',
    )
    self.assertEqual(trigger.max_iterations, 20)
    self.assertEqual(trigger.max_cost_usd, Decimal('0.50'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v -k "limits"`
Expected: FAIL (no `limits` field on `AgentConfigSpec`, no `max_iterations`/`max_cost_usd` on `TriggerSpec`)

- [ ] **Step 3: Implement `SessionLimitsSpec` and update models**

In `backend/libs/agent_spec/spec.py`:

```python
from decimal import Decimal
from pydantic import Field, field_validator

class SessionLimitsSpec(BaseModel):
    """Per-session hard limits declared in agent config YAML."""
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None

    @field_validator('max_iterations')
    @classmethod
    def _positive_iterations(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError('max_iterations must be >= 1')
        return v

    @field_validator('max_cost_usd', mode='before')
    @classmethod
    def _parse_cost(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))

    @field_validator('max_cost_usd')
    @classmethod
    def _positive_cost(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v <= 0:
            raise ValueError('max_cost_usd must be > 0')
        return v
```

Update `AgentConfigSpec`:
```python
class AgentConfigSpec(BaseModel):
    schema_version: Literal[4] = 4
    # ... existing fields ...
    limits: SessionLimitsSpec = Field(default_factory=SessionLimitsSpec)
```

Update `TriggerSpec` — add fields with same validators:
```python
class TriggerSpec(BaseModel):
    # ... existing fields ...
    max_iterations: int | None = None
    max_cost_usd: Decimal | None = None

    @field_validator('max_iterations')
    @classmethod
    def _positive_iterations(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError('max_iterations must be >= 1')
        return v

    @field_validator('max_cost_usd', mode='before')
    @classmethod
    def _parse_cost(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))
```

Update `AGENT_CONFIG_SPEC_VERSION = 4`.

- [ ] **Step 4: Create spec migration `004_session_limits.py`**

```python
# backend/libs/agent_spec/migrations/004_session_limits.py
from __future__ import annotations

FROM_VERSION = 3
TO_VERSION = 4


def upgrade(raw: dict) -> dict:
    """Bump to schema v4; add empty limits block if absent."""
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    if 'limits' not in out or out['limits'] is None:
        out['limits'] = {}
    return out
```

- [ ] **Step 5: Update `__init__.py` exports**

In `backend/libs/agent_spec/__init__.py`, add `SessionLimitsSpec` to the public exports.

- [ ] **Step 6: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v`
Expected: PASS (including existing tests — migration chain intact)

- [ ] **Step 7: Commit and sync (PR-ready chunk)**

```bash
git add backend/libs/agent_spec/ backend/apps/agents/tests/test_spec.py
git commit -m "feat: add SessionLimitsSpec and trigger limit fields (schema v4)"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

## Task 2: Django models — Agent spend limits, SpendPolicy, HourlyUsage

**Files:**
- Modify: `backend/apps/agents/models.py`
- Modify: `backend/apps/sessions/models.py`
- Create: Django migrations (via `makemigrations`)
- Test: `backend/apps/sessions/tests/test_aggregation.py` (model creation only for now)

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/sessions/tests/test_aggregation.py
from decimal import Decimal

from apps.agents.models import Agent, SpendPolicy
from apps.sessions.models import HourlyUsage
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestHourlyUsageModel(OTestCase):
    def test_create_hourly_usage_row(self) -> None:
        user = User.objects.create_user(username='limittest', password='x')
        agent = Agent.objects.create(user=user, name='Test', identifier='test-agent')
        row = HourlyUsage.objects.create(
            agent=agent,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal('0.001'),
            iteration_count=1,
            tool_call_count=2,
        )
        self.assertEqual(row.iteration_count, 1)

    def test_agent_spend_limit_fields(self) -> None:
        user = User.objects.create_user(username='limittest2', password='x')
        agent = Agent.objects.create(
            user=user, name='Test', identifier='test-agent-2',
            daily_spend_limit_usd=Decimal('10.00'),
            monthly_spend_limit_usd=Decimal('100.00'),
        )
        agent.refresh_from_db()
        self.assertEqual(agent.daily_spend_limit_usd, Decimal('10.00'))

    def test_spend_policy_model(self) -> None:
        user = User.objects.create_user(username='limittest3', password='x')
        policy = SpendPolicy.objects.create(
            user=user,
            daily_spend_limit_usd=Decimal('50.00'),
            monthly_spend_limit_usd=Decimal('500.00'),
        )
        self.assertEqual(policy.daily_spend_limit_usd, Decimal('50.00'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py -v`
Expected: FAIL (models don't exist)

- [ ] **Step 3: Add model fields and new models**

In `backend/apps/agents/models.py`:

```python
class Agent(models.Model):
    # ... existing fields ...
    daily_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    monthly_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )


class SpendPolicy(models.Model):
    """Per-user global spend backstop (daily + monthly)."""
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spend_policy',
    )
    daily_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    monthly_spend_limit_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )

    def __str__(self) -> str:
        return f'SpendPolicy(user={self.user_id})'
```

In `backend/apps/sessions/models.py`:

```python
class HourlyUsage(models.Model):
    """Pre-aggregated token and spend totals per agent per model per hour."""
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='hourly_usage')
    hour = models.DateTimeField()
    model = models.CharField(max_length=255)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    cache_creation_input_tokens = models.PositiveBigIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)
    iteration_count = models.PositiveIntegerField(default=0)
    tool_call_count = models.PositiveIntegerField(default=0)

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

    def __str__(self) -> str:
        return f'HourlyUsage({self.agent_id}, {self.hour}, {self.model})'
```

- [ ] **Step 4: Generate Django migrations**

```bash
./olib/scripts/orunr django manage makemigrations agents sessions
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py -v`
Expected: PASS

- [ ] **Step 6: Commit and sync**

```bash
git add backend/apps/agents/ backend/apps/sessions/
git commit -m "feat: add Agent spend limits, SpendPolicy, and HourlyUsage models"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 3: Celery aggregation task

**Files:**
- Modify: `backend/apps/sessions/tasks.py`
- Modify: `backend/chief/celery.py`
- Test: `backend/apps/sessions/tests/test_aggregation.py` (add task tests)

- [ ] **Step 1: Write the failing test**

```python
# Append to backend/apps/sessions/tests/test_aggregation.py

from apps.sessions.models import AgentSession, AgentSessionEvent, AgentSessionEventKind, AgentSessionStatus, TriggerType
from apps.sessions.tasks import aggregate_hourly_usage


class TestAggregateHourlyUsage(OTestCase):
    def _create_session_with_events(self, agent: Agent, cost: Decimal, model: str = 'gpt-5.4-mini') -> None:
        """Helper: create a session with one OUTPUT event carrying the given cost."""
        config = agent.current_config  # assumes agent has a config
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.TRIGGER,
        )
        AgentSessionEvent.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionEventKind.OUTPUT,
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost_usd=cost,
        )

    def test_aggregates_output_events_into_hourly_usage(self) -> None:
        user = User.objects.create_user(username='agg-test', password='x')
        agent = Agent.objects.create(user=user, name='Agg', identifier='agg-agent')
        # Need a config for FK
        from apps.agents.models import AgentConfig
        config = AgentConfig.objects.create(agent=agent, spec={}, spec_version=4)
        agent.current_config = config
        agent.save()

        self._create_session_with_events(agent, Decimal('0.010'))
        self._create_session_with_events(agent, Decimal('0.020'))

        aggregate_hourly_usage()

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.cost_usd, Decimal('0.030000'))
        self.assertEqual(row.iteration_count, 2)
        self.assertEqual(row.input_tokens, 200)

    def test_aggregation_is_idempotent(self) -> None:
        user = User.objects.create_user(username='agg-idem', password='x')
        agent = Agent.objects.create(user=user, name='Idem', identifier='idem-agent')
        from apps.agents.models import AgentConfig
        config = AgentConfig.objects.create(agent=agent, spec={}, spec_version=4)
        agent.current_config = config
        agent.save()

        self._create_session_with_events(agent, Decimal('0.010'))
        aggregate_hourly_usage()
        aggregate_hourly_usage()  # second run should not double-count

        rows = HourlyUsage.objects.filter(agent=agent)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().cost_usd, Decimal('0.010000'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py::TestAggregateHourlyUsage -v`
Expected: FAIL (`aggregate_hourly_usage` doesn't exist)

- [ ] **Step 3: Implement the aggregation task**

In `backend/apps/sessions/tasks.py`:

```python
from decimal import Decimal
from django.db.models import Sum, F
from django.db.models.functions import TruncHour
from django.utils import timezone
from apps.sessions.models import AgentSessionEvent, AgentSessionEventKind, HourlyUsage
from celery import shared_task

@shared_task(ignore_result=True)
def aggregate_hourly_usage() -> None:
    """Roll up recent OUTPUT events into HourlyUsage rows.

    Uses a lookback window (2 hours) and full-replaces the affected hour buckets
    to ensure idempotency without needing a watermark model.
    """
    cutoff = timezone.now() - timezone.timedelta(hours=2)

    aggregates = (
        AgentSessionEvent.objects.filter(
            kind=AgentSessionEventKind.OUTPUT,
            created_at__gte=cutoff,
            cost_usd__isnull=False,
        )
        .annotate(
            hour=TruncHour('created_at'),
            agent_id_fk=F('session__agent_id'),
        )
        .values('agent_id_fk', 'model', 'hour')
        .annotate(
            total_input_tokens=Sum('input_tokens'),
            total_output_tokens=Sum('output_tokens'),
            total_cost=Sum('cost_usd'),
            total_iterations=models.Count('id'),
        )
    )

    # Also count TOOL_CALL events in the same window for tool_call_count
    tool_call_aggregates = (
        AgentSessionEvent.objects.filter(
            kind=AgentSessionEventKind.TOOL_CALL,
            created_at__gte=cutoff,
        )
        .annotate(
            hour=TruncHour('created_at'),
            agent_id_fk=F('session__agent_id'),
        )
        .values('agent_id_fk', 'hour')
        .annotate(total_tool_calls=models.Count('id'))
    )
    # Index tool calls by (agent, hour) for lookup
    tc_map: dict[tuple, int] = {}
    for row in tool_call_aggregates:
        key = (row['agent_id_fk'], row['hour'])
        tc_map[key] = tc_map.get(key, 0) + row['total_tool_calls']

    for row in aggregates:
        agent_id = row['agent_id_fk']
        tc_key = (agent_id, row['hour'])
        HourlyUsage.objects.update_or_create(
            agent_id=agent_id,
            hour=row['hour'],
            model=row['model'] or '',
            defaults={
                'input_tokens': row['total_input_tokens'] or 0,
                'output_tokens': row['total_output_tokens'] or 0,
                'cost_usd': row['total_cost'] or Decimal(0),
                'iteration_count': row['total_iterations'] or 0,
                'tool_call_count': tc_map.get(tc_key, 0),
            },
        )
```

Note: This uses a full-replace strategy within the lookback window — the `update_or_create` overwrites the row with the current aggregate, making it naturally idempotent.

- [ ] **Step 4: Register the beat schedule**

In `backend/chief/celery.py`, add to `beat_schedule`:

```python
'sessions-aggregate-hourly-usage': {
    'task': 'apps.sessions.tasks.aggregate_hourly_usage',
    'schedule': 600.0,  # every 10 minutes
},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py -v`
Expected: PASS

- [ ] **Step 6: Commit and sync**

```bash
git add backend/apps/sessions/ backend/chief/celery.py
git commit -m "feat: add HourlyUsage aggregation celery task (10-min beat)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 4: Budget query helpers and effective cap computation

**Files:**
- Create: `backend/apps/sessions/services/budget.py`
- Test: `backend/apps/sessions/tests/test_budget.py` (new file)

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/sessions/tests/test_budget.py
from decimal import Decimal

from apps.agents.models import Agent, AgentConfig, SpendPolicy
from apps.sessions.models import HourlyUsage
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    compute_effective_spend_cap,
    user_daily_spend,
    user_monthly_spend,
)
from django.contrib.auth import get_user_model
from django.utils import timezone

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestBudgetQueries(OTestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username='budget-user', password='x')
        self.agent = Agent.objects.create(user=self.user, name='B', identifier='budget-agent')
        self.now = timezone.now()
        self.today_hour = self.now.replace(minute=0, second=0, microsecond=0)

    def test_agent_daily_spend_sums_today(self) -> None:
        HourlyUsage.objects.create(
            agent=self.agent, hour=self.today_hour, model='m',
            cost_usd=Decimal('1.50'), iteration_count=10,
        )
        self.assertEqual(agent_daily_spend(self.agent.id), Decimal('1.50'))

    def test_user_daily_spend_sums_across_agents(self) -> None:
        agent2 = Agent.objects.create(user=self.user, name='B2', identifier='budget-agent-2')
        HourlyUsage.objects.create(
            agent=self.agent, hour=self.today_hour, model='m',
            cost_usd=Decimal('1.00'), iteration_count=5,
        )
        HourlyUsage.objects.create(
            agent=agent2, hour=self.today_hour, model='m',
            cost_usd=Decimal('2.00'), iteration_count=5,
        )
        self.assertEqual(user_daily_spend(self.user.id), Decimal('3.00'))


class TestEffectiveSpendCap(OTestCase):
    def test_min_of_all_levels(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=Decimal('5.00'),
            agent_daily_remaining=Decimal('3.00'),
            agent_monthly_remaining=Decimal('50.00'),
            user_daily_remaining=Decimal('10.00'),
            user_monthly_remaining=Decimal('100.00'),
        )
        self.assertEqual(result, Decimal('3.00'))

    def test_none_values_ignored(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=Decimal('5.00'),
            agent_daily_remaining=None,
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )
        self.assertEqual(result, Decimal('5.00'))

    def test_all_none_returns_none(self) -> None:
        result = compute_effective_spend_cap(
            session_spend_cap=None,
            agent_daily_remaining=None,
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )
        self.assertIsNone(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_budget.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Implement budget helpers**

Create `backend/apps/sessions/services/__init__.py` (if not exists) and `backend/apps/sessions/services/budget.py`:

```python
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
        agent_id=agent_id, hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)


def agent_monthly_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id, hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)


def user_daily_spend(user_id: int) -> Decimal:
    """Sum spend across all agents for a user for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent__user_id=user_id, hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)


def user_monthly_spend(user_id: int) -> Decimal:
    """Sum spend across all agents for a user for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent__user_id=user_id, hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)


def compute_effective_spend_cap(
    *,
    session_spend_cap: Decimal | None,
    agent_daily_remaining: Decimal | None,
    agent_monthly_remaining: Decimal | None,
    user_daily_remaining: Decimal | None,
    user_monthly_remaining: Decimal | None,
) -> Decimal | None:
    """Return the tightest spend cap across all levels, or None if uncapped."""
    candidates = [
        v for v in (
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/sessions/services/
git commit -m "feat: budget query helpers and effective spend cap computation"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 5: Session limit checker and loop integration

**Files:**
- Create: `backend/apps/runner/limits.py`
- Modify: `backend/apps/runner/loop.py`
- Modify: `backend/apps/runner/errors.py`
- Modify: `backend/chief/settings.py`
- Test: `backend/apps/runner/tests/test_limits.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/runner/tests/test_limits.py
from decimal import Decimal
from unittest.mock import patch

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.loop import SessionRunner
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from libs.agent_spec import AgentConfigSpec, LLMSpec, SessionLimitsSpec
from libs.providers.llm.base import StreamResult, Usage
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase


class TestSessionIterationLimit(OTestCase):
    def _spec_with_limits(self, max_iterations: int = 3) -> AgentConfigSpec:
        return AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits=SessionLimitsSpec(max_iterations=max_iterations),
        )

    def test_halts_at_iteration_limit(self) -> None:
        spec = self._spec_with_limits(max_iterations=2)
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        responses = [
            StreamResult(content='one', usage=Usage(model='fake', input_tokens=10, output_tokens=10)),
            StreamResult(content='two', usage=Usage(model='fake', input_tokens=10, output_tokens=10)),
            StreamResult(content='three', usage=Usage(model='fake', input_tokens=10, output_tokens=10)),
        ]
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            SessionRunner(backend).run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertEqual(len(failure_events), 1)
        self.assertEqual(failure_events[0].payload['code'], 'session_iteration_limit')
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)


class TestSessionSpendLimit(OTestCase):
    def test_halts_at_spend_limit(self) -> None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits=SessionLimitsSpec(max_cost_usd=Decimal('0.05')),
        )
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        responses = [
            StreamResult(
                content='expensive',
                usage=Usage(model='gpt-5.4-mini', input_tokens=50000, output_tokens=50000),
            ),
            StreamResult(
                content='more',
                usage=Usage(model='gpt-5.4-mini', input_tokens=50000, output_tokens=50000),
            ),
        ]
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            SessionRunner(backend).run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertTrue(len(failure_events) >= 1)
        codes = [e.payload['code'] for e in failure_events]
        self.assertIn('session_spend_limit', codes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_limits.py -v`
Expected: FAIL (no limit enforcement in the loop)

- [ ] **Step 3: Add limit-breach failure classes**

In `backend/apps/runner/errors.py`:

```python
class SessionIterationLimitExceeded(SessionFailure):
    def __init__(self, limit: int) -> None:
        super().__init__(
            f'Session iteration limit reached ({limit})',
            code='session_iteration_limit',
        )


class SessionSpendLimitExceeded(SessionFailure):
    def __init__(self, limit: str) -> None:
        super().__init__(
            f'Session spend limit reached (${limit})',
            code='session_spend_limit',
        )


class AgentDailySpendLimitExceeded(SessionFailure):
    def __init__(self) -> None:
        super().__init__('Agent daily spend budget exceeded', code='agent_daily_spend_limit')


class AgentMonthlySpendLimitExceeded(SessionFailure):
    def __init__(self) -> None:
        super().__init__('Agent monthly spend budget exceeded', code='agent_monthly_spend_limit')


class UserDailySpendLimitExceeded(SessionFailure):
    def __init__(self) -> None:
        super().__init__('User daily spend budget exceeded', code='user_daily_spend_limit')


class UserMonthlySpendLimitExceeded(SessionFailure):
    def __init__(self) -> None:
        super().__init__('User monthly spend budget exceeded', code='user_monthly_spend_limit')
```

- [ ] **Step 4: Add Django settings defaults**

In `backend/chief/settings.py`:

```python
from decimal import Decimal

# Session limits — global defaults (narrowing hierarchy floor)
DEFAULT_MAX_SESSION_ITERATIONS: int | None = 200
DEFAULT_MAX_SESSION_COST_USD: Decimal | None = Decimal('5.00')

# Agent rolling spend defaults
DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None

# User rolling spend defaults
DEFAULT_USER_DAILY_SPEND_LIMIT_USD: Decimal | None = None
DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD: Decimal | None = None
```

- [ ] **Step 5: Implement `SessionLimitChecker`**

Create `backend/apps/runner/limits.py`:

```python
"""Session limit checker — computes effective caps and runs pre-iteration checks.

Collapses the narrowing hierarchy + rolling budgets into two in-memory values
(effective_max_iterations and effective_spend_cap) that are compared every iteration
with zero DB queries. Long-running sessions refresh the spend cap periodically.
"""
from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID

from django.conf import settings

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
from libs.agent_spec import AgentConfigSpec, TriggerSpec

BUDGET_REFRESH_INTERVAL_S = 300  # 5 minutes


def _min_non_none(*values: int | None) -> int | None:
    """Return the smallest non-None value, or None if all are None."""
    candidates = [v for v in values if v is not None]
    return min(candidates) if candidates else None


class SessionLimitChecker:
    """Tracks iteration count and spend, enforces limits in-memory."""

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
        self.effective_max_iterations = _min_non_none(
            getattr(settings, 'DEFAULT_MAX_SESSION_ITERATIONS', None),
            spec.limits.max_iterations,
            trigger_max_iter,
        )

        # Compute session-level spend cap from narrowing hierarchy
        trigger_max_cost = trigger_spec.max_cost_usd if trigger_spec else None
        self._session_spend_cap = compute_effective_spend_cap(
            session_spend_cap=_decimal_min_non_none(
                getattr(settings, 'DEFAULT_MAX_SESSION_COST_USD', None),
                spec.limits.max_cost_usd,
                trigger_max_cost,
            ),
            agent_daily_remaining=None,
            agent_monthly_remaining=None,
            user_daily_remaining=None,
            user_monthly_remaining=None,
        )

        # Compute full effective spend cap including rolling budgets
        self._last_budget_snapshot = time.monotonic()
        self.effective_spend_cap = self._compute_effective_spend_cap()

    def _compute_effective_spend_cap(self) -> Decimal | None:
        """Query HourlyUsage and compute the tightest spend cap."""
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
        """Run all limit checks. Raises SessionFailure on breach."""
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
        """Re-snapshot rolling budgets for long-running sessions."""
        now = time.monotonic()
        if now - self._last_budget_snapshot < BUDGET_REFRESH_INTERVAL_S:
            return
        self.effective_spend_cap = self._compute_effective_spend_cap()
        self._last_budget_snapshot = now


def _decimal_min_non_none(*values: Decimal | None) -> Decimal | None:
    """Return the smallest non-None Decimal, or None if all are None."""
    candidates = [v for v in values if v is not None]
    return min(candidates) if candidates else None
```

- [ ] **Step 6: Wire into `SessionRunner.run()` loop**

In `backend/apps/runner/loop.py`:

1. Import `SessionLimitChecker` from `apps.runner.limits`.
2. In `SessionRunner.__init__`, create `self._limit_checker` (passing spec, trigger info from the backend's session if available, agent/user budget fields).
3. In the while-loop, **before** `provider.collect()`, call `self._limit_checker.check()`.
4. After `self._emit_output()`, call `self._limit_checker.record_iteration()` and `self._limit_checker.record_cost(cost)`.

The `check()` call is inside the try/except that already catches `SessionFailure` via `_record_failure`, so breaches are handled by the existing path.

- [ ] **Step 7: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_limits.py -v`
Expected: PASS

Also run the full loop test suite to ensure no regressions:
Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 8: Commit and sync**

```bash
git add backend/apps/runner/ backend/chief/settings.py
git commit -m "feat: session limit checker with iteration + spend enforcement"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 6: Scheduling gate — budget checks before dispatch

**Files:**
- Modify: `backend/apps/runner/scheduling.py`
- Modify: `backend/apps/runner/tests/test_scheduling.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to backend/apps/runner/tests/test_scheduling.py

from decimal import Decimal
from apps.agents.models import SpendPolicy
from apps.sessions.models import HourlyUsage
from django.utils import timezone


class TestBudgetGateScheduling(OTestCase):
    """Verify that dispatch is blocked when rolling budgets are exceeded."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username='gate-test', password='x')
        self.agent = Agent.objects.create(
            user=self.user, name='Gate', identifier='gate-agent',
            daily_spend_limit_usd=Decimal('1.00'),
        )
        spec = _minimal_spec(triggers=[_schedule_trigger()])
        persist_agent_config(self.agent, spec, source_rev='test')
        self.trigger = Trigger.objects.get(agent=self.agent)

    def test_dispatch_blocked_when_agent_daily_budget_exceeded(self) -> None:
        HourlyUsage.objects.create(
            agent=self.agent,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='m',
            cost_usd=Decimal('1.50'),
            iteration_count=10,
        )
        result = dispatch_schedule_trigger(trigger_id=self.trigger.pk)
        self.assertFalse(result)
        self.assertEqual(AgentSession.objects.filter(agent=self.agent).count(), 0)

    def test_dispatch_allowed_when_under_budget(self) -> None:
        HourlyUsage.objects.create(
            agent=self.agent,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='m',
            cost_usd=Decimal('0.50'),
            iteration_count=5,
        )
        with patch('apps.runner.scheduling.push_chat_and_dispatch'):
            result = dispatch_schedule_trigger(trigger_id=self.trigger.pk)
        self.assertTrue(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_scheduling.py::TestBudgetGateScheduling -v`
Expected: FAIL (no budget gate in dispatch)

- [ ] **Step 3: Implement budget gate**

In `backend/apps/runner/scheduling.py`, add a helper:

```python
from apps.sessions.services.budget import agent_daily_spend, agent_monthly_spend, user_daily_spend, user_monthly_spend
from apps.agents.models import SpendPolicy

def _budget_allows_dispatch(agent: Agent) -> bool:
    """Check agent and user rolling spend budgets before dispatch."""
    from django.conf import settings

    # Agent daily
    agent_daily_limit = agent.daily_spend_limit_usd or getattr(settings, 'DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD', None)
    if agent_daily_limit is not None:
        if agent_daily_spend(agent.id) >= agent_daily_limit:
            logger.warning('Budget gate: agent %s daily spend exceeded', agent.identifier)
            return False

    # Agent monthly
    agent_monthly_limit = agent.monthly_spend_limit_usd or getattr(settings, 'DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD', None)
    if agent_monthly_limit is not None:
        if agent_monthly_spend(agent.id) >= agent_monthly_limit:
            logger.warning('Budget gate: agent %s monthly spend exceeded', agent.identifier)
            return False

    # User daily
    user_daily_limit = getattr(settings, 'DEFAULT_USER_DAILY_SPEND_LIMIT_USD', None)
    try:
        policy = SpendPolicy.objects.get(user_id=agent.user_id)
        user_daily_limit = policy.daily_spend_limit_usd or user_daily_limit
    except SpendPolicy.DoesNotExist:
        pass
    if user_daily_limit is not None:
        if user_daily_spend(agent.user_id) >= user_daily_limit:
            logger.warning('Budget gate: user %s daily spend exceeded', agent.user_id)
            return False

    # User monthly
    user_monthly_limit = getattr(settings, 'DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD', None)
    try:
        policy = SpendPolicy.objects.get(user_id=agent.user_id)
        user_monthly_limit = policy.monthly_spend_limit_usd or user_monthly_limit
    except SpendPolicy.DoesNotExist:
        pass
    if user_monthly_limit is not None:
        if user_monthly_spend(agent.user_id) >= user_monthly_limit:
            logger.warning('Budget gate: user %s monthly spend exceeded', agent.user_id)
            return False

    return True
```

Insert `_budget_allows_dispatch(agent)` check in `dispatch_schedule_trigger` (after validating the trigger is active but before the `transaction.atomic()` block) and in `_fill_queue_trigger_slots` (before taking items).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_scheduling.py -v`
Expected: PASS (both new and existing tests)

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/runner/scheduling.py backend/apps/runner/tests/test_scheduling.py
git commit -m "feat: budget gate blocks dispatch when rolling spend exceeded"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 7: Full integration test and gate verification

**Files:**
- Modify: `backend/apps/runner/tests/test_limits.py` (add rolling budget integration)

- [ ] **Step 1: Write integration test for rolling budget enforcement in the loop**

```python
# Append to backend/apps/runner/tests/test_limits.py

from apps.agents.models import Agent, AgentConfig
from apps.sessions.models import HourlyUsage
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class TestRollingBudgetEnforcement(OTestCase):
    """Integration: verify the loop halts when the effective spend cap
    is already consumed (rolling budget tight enough to block on first check)."""

    def test_loop_halts_when_agent_daily_budget_blown(self) -> None:
        user = User.objects.create_user(username='rolling-test', password='x')
        agent = Agent.objects.create(
            user=user, name='R', identifier='rolling-agent',
            daily_spend_limit_usd=Decimal('0.01'),
        )
        # Populate HourlyUsage so agent is already over budget
        HourlyUsage.objects.create(
            agent=agent,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-mini',
            cost_usd=Decimal('0.50'),
            iteration_count=10,
        )
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
        )
        backend = MemorySessionBackend(spec, user_id=user.id)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        # The limit checker needs agent context — this tests the wiring
        # through SessionRunner with the agent_id/user_id path
        runner = SessionRunner(backend, agent_id=agent.id)
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([
            StreamResult(content='x', usage=Usage(model='fake', input_tokens=1, output_tokens=1)),
        ])):
            runner.run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertTrue(len(failure_events) >= 1)
        codes = [e.payload['code'] for e in failure_events]
        self.assertTrue(
            any(c in codes for c in ('session_spend_limit', 'agent_daily_spend_limit')),
        )
```

- [ ] **Step 2: Run full test suite**

Run: `./olib/scripts/orunr py test-all`
Expected: PASS

- [ ] **Step 3: Commit and sync**

```bash
git add backend/apps/runner/tests/test_limits.py
git commit -m "test: rolling budget enforcement integration test"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 8: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

**Files:** (review only — no edits unless user requests fixes)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Read `superpowers/requesting-code-review` skill. Dispatch reviewer subagent with:

- `{DESCRIPTION}` — Session iteration + spend limits with narrowing hierarchy, HourlyUsage aggregation, and budget gates
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-15-session-limits/2026-07-15-session-limits-design.md` and `docs/specs/2026-07-15-session-limits/2026-07-15-session-limits-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Read `superpowers/requesting-code-review` skill and **`review-file-template.md`**.

1. Write `docs/specs/2026-07-15-session-limits/2026-07-15-session-limits-review.md` (same prefix as `-design.md` / `-plan.md`).
2. One issue table per severity with columns: `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**.
3. Summarize the same content in chat (assessment + tables).

Stop here unless the user asks to fix issues.

- [ ] **Step 5: Track feedback**

When the user requests fixes or rejects findings, update **Status** in `*-review.md`:

- **Fixed** — after implementing the fix
- **Rejected** — when the user declines; record rationale in **Notes**

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` (PR / merge options). Do **not** check epic/spec boxes in `-revision.md` or the epic file unless the user explicitly approves after review.

---

## Out of scope

- UI dashboard for viewing/configuring limits (separate ticket).
- Alerting/webhook on budget breach (future work per design non-goals).
- Per-tool-call cost budgets.
- `cached_input_tokens` / `cache_creation_input_tokens` in `HourlyUsage` (columns exist for future use but aggregation task focuses on totals from OUTPUT events for now).
