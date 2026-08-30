# Background algorithms — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-30-background-algorithms/2026-08-30-background-algorithms-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill). Under `/ship`, the ship skill owns S_final and PR; executors stop after implementation tasks.

**Goal:** Give code-registered background algorithms (v1: `chat_name`) operator sessions, activity traces, and user-level spend without Agent rows, without mixing them into Recent sessions or agent totals, and without a second session when an algorithm runs under an in-tool recorder.

**Architecture:** Keep a single `AgentSession` / `HourlyUsage` table. Every session and usage row has a required `user` FK (backfill existing from `agent.user`). Owner is XOR: agent+config **or** `algorithm_id` (registry string, not an Agent). No `target_session` column — optional `target_session_id` lives on the algorithm session’s **root activity `details`**. One-off Celery jobs create an algorithm-owned session and record traces there; in-tool callers inject `ActivityRecorder` only. User spend sums `HourlyUsage.user_id`; agent spend stays `agent_id` (algorithm buckets excluded). Dashboard Recent sessions filter `agent_id` not null.

**Tech Stack:** Django 5, PostgreSQL check/unique constraints, Celery, Jinja+htmx dashboard, `libs.algorithms` (Django-free), existing `ActivityRecorder` + `BackendActivityRecorder`.

**Branch:** `feat/2026-08-30-background-algorithms`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …` while iterating; `./olib/scripts/orunr py test-all` before S_final
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-30-background-algorithms`. After each stage commit: `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change (purpose + assumptions)
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`ai/commands/py-checks.md`)
- **CLI stdout:** capture with `self.captureStdout()` and assert (`ai/commands/py-checks.md`)
- **Test names:** do not use the words error / exception / warning / deprecated (parproc). Prefer failure / raises / invalid / skipped / legacy
- **Do not add license headers** — pre-commit adds them
- **Django schema:** generate migrations with `./olib/scripts/orunr django manage makemigrations`; do not hand-write the schema file from scratch. After generate, insert a `RunPython` backfill before `null=False`
- **Views:** no new ORM in `apps.web.views` — queries/commands only (`docs/ARCHITECTURE.md`)
- **Libs:** `libs.algorithms` stays Django-free (no `apps.*` imports)
- **No Agent rows for algorithms.** No `target_session` FK/field. No algorithm YAML. No `run_session` for algorithm jobs
- **Final task:** code review via **`superpowers/requesting-code-review`** (see mandatory **S_final**)

---

## Locked decisions (do not reopen)

| Decision | Implementation |
|----------|----------------|
| Required `user` on **all** sessions and **all** `HourlyUsage` rows | Backfill from `agent.user`; `save()` copies `agent.user_id` on agent-session create if `user_id` omitted |
| Owner XOR | Exactly one of: (`agent` + `agent_config`, `algorithm_id` null) **or** (`algorithm_id` set, `agent`/`agent_config` null, `user` set, `parent_session` null) |
| No Algorithm Django model | `algorithm_id` is a registry string (`chat_name`) |
| No target session field | If useful, `details['target_session_id']` on the **root** activity of the algorithm session |
| Traces | Algorithm session owns `llm`/`span` rows; chat session is only patched via `update_session_name` |
| Spend | `user_*_spend` filters `user_id`; `agent_*_spend` filters `agent_id` (null on algorithm buckets) |
| Recent sessions | `agent_id__isnull=False` only |
| In-tool | Inject `ActivityRecorder`; **do not** call `create_algorithm_session` |

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/libs/algorithms/registry.py` | Create | Frozen catalog: id, display name, config type |
| `backend/libs/algorithms/__init__.py` | Modify | Export registry + chat-name types; no re-export shims of deleted modules |
| `backend/libs/algorithms/chat_name.py` | Modify | Optional `ActivityRecorder`; return `ChatNameResult` |
| `backend/libs/algorithms/tests/test_registry.py` | Create | Registry listing and lookup |
| `backend/libs/algorithms/tests/test_chat_name.py` | Modify | Result type + recorder nesting (no Django session) |
| `backend/apps/sessions/models.py` | Modify | `user`, `algorithm_id`, nullable agent/config, XOR checks, freeze, ancestry |
| `backend/apps/sessions/migrations/0011_*.py` | Generate | Nullable fields + backfill |
| `backend/apps/sessions/migrations/0012_*.py` | Generate | `user` required, unique/check constraints, indexes |
| `backend/apps/sessions/admin.py` | Modify | Show `user`, `algorithm_id` |
| `backend/apps/sessions/tests/base.py` | Modify | Autofill `user`; add `make_algorithm_session` |
| `backend/apps/sessions/tests/test_session_owner.py` | Create | XOR, freeze, algorithm ancestry skip |
| `backend/apps/sessions/tests/test_budget.py` | Modify | `user=` on usage; algorithm included in user totals, excluded from agent |
| `backend/apps/sessions/tests/test_aggregation.py` | Modify | Dual buckets; `user` on creates |
| `backend/apps/sessions/services/budget.py` | Modify | User filter by `user_id`; algorithm spend helpers; user-cap helper |
| `backend/apps/sessions/services/commands.py` | Modify | `create_algorithm_session`, finish helper |
| `backend/apps/sessions/services/queries.py` | Modify | Find chat-name run by `target_session_id` in details |
| `backend/apps/sessions/tasks.py` | Modify | Dual usage rollup; `generate_session_name` algorithm session |
| `backend/apps/runner/backends/django.py` | Modify | `user_id` from `session.user_id` |
| `backend/apps/runner/budget_gate.py` | Modify | Delegate user caps to sessions budget helper (DRY) |
| `backend/apps/runner/tests/test_budget_gate.py` | Modify | `user=` on `HourlyUsage` creates |
| `backend/apps/runner/tests/test_limits_integration.py` | Modify | `user=` on `HourlyUsage` creates |
| `backend/apps/web/services/queries.py` | Modify | Recents agent-only; owned session by `user_id`; background/algorithm detail DTOs |
| `backend/apps/web/views.py` | Modify | Background context; algorithm detail; algorithm session chrome |
| `backend/apps/web/urls.py` | Modify | `/algorithms/<slug:algorithm_id>/` |
| `backend/templates/web/dashboard.html` | Modify | Background card between Agents and Usage |
| `backend/templates/web/algorithm_detail.html` | Create | Usage + sessions for one registry id |
| `backend/templates/web/session_detail.html` | Modify | Algorithm mode: no composer / agent chrome |
| `backend/templates/web/layout/agent_frame_page.html` | Modify | Optional composer include |
| `backend/apps/web/tests/test_background.py` | Create | Catalog, recents exclusion, algorithm page, session chrome |
| `backend/apps/sessions/tests/test_tasks.py` | Modify | Algorithm session + cost; idempotent named chat |
| `docs/ARCHITECTURE.md` | Modify | Owner XOR, usage buckets, Background, import edge |

---

### Task 1: Algorithm registry in `libs.algorithms`

**Files:**
- Create: `backend/libs/algorithms/registry.py`
- Modify: `backend/libs/algorithms/__init__.py`
- Create: `backend/libs/algorithms/tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
from libs.algorithms.registry import get_algorithm, list_algorithms

from olib.py.django.test.cases import OTestCase


class TestAlgorithmRegistry(OTestCase):
    def test_lists_chat_name(self) -> None:
        ids = [item.algorithm_id for item in list_algorithms()]
        self.assertEqual(ids, ['chat_name'])
        self.assertEqual(get_algorithm('chat_name').display_name, 'Chat name')

    def test_unknown_id_returns_none(self) -> None:
        self.assertIsNone(get_algorithm('not_a_registered_algorithm'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/libs/algorithms/tests/test_registry.py -v`

Expected: FAIL (`ModuleNotFoundError` or import failure for `libs.algorithms.registry`)

- [ ] **Step 3: Implement the registry**

`backend/libs/algorithms/registry.py`:

```python
"""Code catalog of background algorithms (not Django models, not Agents)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlgorithmInfo:
    """Stable registry row listed on the dashboard Background card."""

    algorithm_id: str
    display_name: str


CHAT_NAME_ID = 'chat_name'

ALGORITHMS: tuple[AlgorithmInfo, ...] = (
    AlgorithmInfo(algorithm_id=CHAT_NAME_ID, display_name='Chat name'),
)

_BY_ID = {item.algorithm_id: item for item in ALGORITHMS}


def list_algorithms() -> tuple[AlgorithmInfo, ...]:
    """Return every registered algorithm, including those with zero runs."""
    return ALGORITHMS


def get_algorithm(algorithm_id: str) -> AlgorithmInfo | None:
    """Return the registry row for ``algorithm_id``, or None if unknown."""
    return _BY_ID.get(algorithm_id)
```

Update `backend/libs/algorithms/__init__.py` to export `AlgorithmInfo`, `CHAT_NAME_ID`, `get_algorithm`, `list_algorithms` alongside existing chat-name symbols. Do not add a Django `Algorithm` table.

- [ ] **Step 4: Run test to verify it passes**

Run: `./olib/scripts/orunr py test backend/libs/algorithms/tests/test_registry.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync (PR-ready chunk)**

```bash
git add backend/libs/algorithms/registry.py backend/libs/algorithms/__init__.py backend/libs/algorithms/tests/test_registry.py
git commit -m "feat: add libs.algorithms registry for background jobs"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 2: Session and HourlyUsage owner XOR + required user

**Files:**
- Modify: `backend/apps/sessions/models.py`
- Modify: `backend/apps/sessions/admin.py`
- Modify: `backend/apps/sessions/tests/base.py`
- Create: `backend/apps/sessions/tests/test_session_owner.py`
- Modify: `backend/apps/sessions/tests/test_budget.py` (add `user=` so creates keep working)
- Modify: `backend/apps/sessions/tests/test_aggregation.py` (`user=` on `HourlyUsage`)
- Modify: `backend/apps/runner/tests/test_budget_gate.py`
- Modify: `backend/apps/runner/tests/test_limits_integration.py`
- Modify: `backend/apps/runner/backends/django.py`
- Generate: `backend/apps/sessions/migrations/0011_*` and `0012_*`

- [ ] **Step 1: Write failing owner tests**

Create `backend/apps/sessions/tests/test_session_owner.py`:

```python
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from apps.sessions.tests.base import make_algorithm_session, make_test_session
from libs.algorithms.registry import CHAT_NAME_ID

from olib.py.django.test.cases import OTransactionTestCase


class TestSessionOwnerXor(OTransactionTestCase):
    def test_agent_session_requires_user_matching_agent(self) -> None:
        session = make_test_session('owner-agent')
        session.refresh_from_db()
        self.assertEqual(session.user_id, session.agent.user_id)
        self.assertIsNone(session.algorithm_id)

    def test_algorithm_session_has_null_agent_and_config(self) -> None:
        session = make_algorithm_session('owner-algo')
        self.assertIsNone(session.agent_id)
        self.assertIsNone(session.agent_config_id)
        self.assertEqual(session.algorithm_id, CHAT_NAME_ID)
        self.assertIsNotNone(session.user_id)
        self.assertIsNone(session.parent_session_id)
        self.assertEqual(session.trigger_type, TriggerType.ALGORITHM)

    def test_rejects_both_agent_and_algorithm(self) -> None:
        session = make_test_session('owner-both')
        session.algorithm_id = CHAT_NAME_ID
        with self.assertRaises(ValidationError):
            session.save()

    def test_db_rejects_algorithm_without_user(self) -> None:
        user = get_user_model().objects.create_user(username='xor-nouser', password='x')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentSession.objects.create(
                user=None,
                algorithm_id=CHAT_NAME_ID,
                agent=None,
                agent_config=None,
                status=AgentSessionStatus.RUNNING,
                trigger_type=TriggerType.ALGORITHM,
            )
        del user

    def test_algorithm_skips_agent_config_ancestry(self) -> None:
        session = make_algorithm_session('owner-no-config')
        session.status = AgentSessionStatus.DONE
        session.save(update_fields=['status'])

    def test_algorithm_id_is_immutable(self) -> None:
        session = make_algorithm_session('owner-freeze')
        session.algorithm_id = 'other'
        with self.assertRaises(ValidationError):
            session.save()
```

Add to `backend/apps/sessions/tests/base.py`:

```python
def make_algorithm_session(identifier: str = 'algo', *, algorithm_id: str = 'chat_name') -> AgentSession:
    """Create an algorithm-owned session for the given user identifier (no Agent)."""
    user = get_user_model().objects.create_user(username=f'user-{identifier}', password='test')
    return AgentSession.objects.create(
        user=user,
        algorithm_id=algorithm_id,
        agent=None,
        agent_config=None,
        parent_session=None,
        status=AgentSessionStatus.RUNNING,
        trigger_type=TriggerType.ALGORITHM,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_session_owner.py -v`

Expected: FAIL (`TriggerType.ALGORITHM` missing, `user`/`algorithm_id` missing, `make_algorithm_session` create fails)

- [ ] **Step 3: Implement model changes**

In `TriggerType` add `ALGORITHM = 'algorithm', 'Algorithm'`.

On `AgentSession`:

- `user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='agent_sessions')` — after migration, **required** (`null=False`)
- `agent` and `agent_config`: `null=True, blank=True`
- `algorithm_id = models.CharField(max_length=64, null=True, blank=True)`
- Index `['user', 'algorithm_id', '-created_at']`
- CheckConstraint `sessions_agentsession_owner_xor`:

```python
models.CheckConstraint(
    name='sessions_agentsession_owner_xor',
    condition=(
        models.Q(agent_id__isnull=False, agent_config_id__isnull=False, algorithm_id__isnull=True)
        | models.Q(
            agent_id__isnull=True,
            agent_config_id__isnull=True,
            algorithm_id__isnull=False,
            parent_session_id__isnull=True,
        )
    ),
)
```

`save()`:

- On **add**, if `agent_id` is set and `user_id` is None: set `user_id` from `self.agent.user_id`
- If agent session: `user_id` must equal `agent.user_id`; `algorithm_id` must be None; keep existing config-ancestry checks
- If algorithm session: `algorithm_id` must be in `list_algorithms()` ids; skip `_validate_locked_ancestry` agent-config checks; reject `parent_session_id`
- Extend freeze set with `user`, `user_id`, `algorithm_id`; compare those on update like `agent_id`

`__str__`: if `algorithm_id`: `f'{self.algorithm_id} session {self.id}'` else existing agent form.

On `HourlyUsage`:

- Required `user` FK (`related_name='hourly_usage'`)
- `agent` nullable (`null=True, blank=True`)
- `algorithm_id` nullable CharField(max_length=64)
- Replace unique `(agent, hour, model)` with:
  - `UniqueConstraint(fields=['agent', 'hour', 'model'], condition=Q(agent_id__isnull=False), name='sessions_hourlyusage_agent_hour_model_uniq')`
  - `UniqueConstraint(fields=['user', 'algorithm_id', 'hour', 'model'], condition=Q(algorithm_id__isnull=False), name='sessions_hourlyusage_algo_hour_model_uniq')`
- CheckConstraint `sessions_hourlyusage_owner_xor`: agent set XOR `algorithm_id` set (same pattern as sessions; `user_id` always set)

`DjangoSessionBackend.user_id`: `return self._session.user_id` (works for both owners).

Admin: add `user`, `algorithm_id` to `list_display` / `list_filter`.

Every existing `HourlyUsage.objects.create(...)` in tests must pass `user=agent.user` (or the billed user). Agent-session `AgentSession.objects.create` may omit `user` because `save()` fills it.

- [ ] **Step 4: Generate migrations (do not hand-write schema)**

Implement fields first as **nullable** `user` if makemigrations would otherwise demand a default, then:

```bash
./olib/scripts/orunr django manage makemigrations agent_sessions --name session_algorithm_owner_nullable
```

Edit the generated file: after `AddField`/`AlterField` that introduce `user` and `algorithm_id`, insert:

```python
def backfill_session_and_usage_users(apps, schema_editor):
    """Copy agent.user onto existing session and hourly usage rows."""
    AgentSession = apps.get_model('sessions', 'AgentSession')
    HourlyUsage = apps.get_model('sessions', 'HourlyUsage')
    for row in AgentSession.objects.exclude(agent_id=None).select_related('agent').iterator():
        if row.user_id is None:
            AgentSession.objects.filter(pk=row.pk).update(user_id=row.agent.user_id)
    for row in HourlyUsage.objects.exclude(agent_id=None).select_related('agent').iterator():
        if row.user_id is None:
            HourlyUsage.objects.filter(pk=row.pk).update(user_id=row.agent.user_id)


def noop_reverse(apps, schema_editor):
    """User FKs stay populated on reverse; constraints drop in the later migration."""
    del apps, schema_editor
```

`migrations.RunPython(backfill_session_and_usage_users, noop_reverse)`.

Then set `user` to `null=False` on both models, add constraints, and:

```bash
./olib/scripts/orunr django manage makemigrations agent_sessions --name session_algorithm_owner_constraints
```

- [ ] **Step 5: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_session_owner.py backend/apps/sessions/tests/test_budget.py backend/apps/sessions/tests/test_aggregation.py backend/apps/runner/tests/test_budget_gate.py -v`

Expected: owner tests PASS. Budget/aggregation may still fail until Task 3–4 if they assert `agent__user_id` semantics; if they only needed `user=` on create, they PASS.

- [ ] **Step 6: Commit and sync**

```bash
git add backend/apps/sessions/models.py backend/apps/sessions/admin.py backend/apps/sessions/migrations backend/apps/sessions/tests backend/apps/runner/backends/django.py backend/apps/runner/tests/test_budget_gate.py backend/apps/runner/tests/test_limits_integration.py
git commit -m "feat: require session user and XOR agent vs algorithm owner"
git fetch origin main
git rebase origin/main
git push
```

---

### Task 3: Budget queries — user includes algorithms; agent excludes them

**Files:**
- Modify: `backend/apps/sessions/services/budget.py`
- Modify: `backend/apps/sessions/tests/test_budget.py`
- Modify: `backend/apps/runner/budget_gate.py`

- [ ] **Step 1: Write failing tests** (append to `TestBudgetQueries`)

```python
def test_user_daily_spend_includes_algorithm_bucket(self) -> None:
    HourlyUsage.objects.create(
        user=self.user,
        agent=None,
        algorithm_id='chat_name',
        hour=self.today_hour,
        model='gpt-5.4-nano',
        cost_usd=Decimal('0.400000'),
    )
    HourlyUsage.objects.create(
        user=self.user,
        agent=self.agent,
        algorithm_id=None,
        hour=self.today_hour,
        model='m',
        cost_usd=Decimal('1.000000'),
        iteration_count=1,
    )
    self.assertEqual(user_daily_spend(self.user.id), Decimal('1.400000'))
    self.assertEqual(agent_daily_spend(self.agent.id), Decimal('1.000000'))


def test_algorithm_daily_spend_is_scoped_to_user_and_id(self) -> None:
    other = User.objects.create_user(username='budget-other', password='x')
    HourlyUsage.objects.create(
        user=self.user,
        agent=None,
        algorithm_id='chat_name',
        hour=self.today_hour,
        model='m',
        cost_usd=Decimal('0.250000'),
    )
    HourlyUsage.objects.create(
        user=other,
        agent=None,
        algorithm_id='chat_name',
        hour=self.today_hour,
        model='m',
        cost_usd=Decimal('9.000000'),
    )
    self.assertEqual(algorithm_daily_spend(self.user.id, 'chat_name'), Decimal('0.250000'))
```

Also add `test_user_rolling_cap_reached_when_daily_met` using `SpendPolicy` + `user_rolling_cap_reached(user_id)`.

Existing `HourlyUsage.objects.create` in this file must include `user=self.user` (Task 2). Agent creates keep `algorithm_id=None`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_budget.py -v -k "algorithm or rolling"`

Expected: FAIL (`algorithm_daily_spend` missing; `user_daily_spend` still uses `agent__user_id` and misses algorithm rows)

- [ ] **Step 3: Implement**

```python
def agent_daily_spend(agent_id: UUID) -> Decimal:
    """Sum spend from HourlyUsage for this agent for the current UTC day.

    Algorithm buckets have null agent_id and are excluded.
    """
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        agent_id=agent_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)
```

Same pattern for `agent_monthly_spend`.

```python
def user_daily_spend(user_id: int) -> Decimal:
    """Sum agent and algorithm HourlyUsage for this user for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)
```

Same for `user_monthly_spend` (replace `agent__user_id`).

```python
def algorithm_daily_spend(user_id: int, algorithm_id: str) -> Decimal:
    """Sum this user's spend for one registry algorithm for the current UTC day."""
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        algorithm_id=algorithm_id,
        hour__gte=today_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)


def algorithm_monthly_spend(user_id: int, algorithm_id: str) -> Decimal:
    """Sum this user's spend for one registry algorithm for the current UTC month."""
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return HourlyUsage.objects.filter(
        user_id=user_id,
        algorithm_id=algorithm_id,
        hour__gte=month_start,
    ).aggregate(total=Sum('cost_usd'))['total'] or Decimal(0)
```

Move `_resolve_user_caps` from `apps.runner.budget_gate` into `budget.py` as `resolve_user_spend_caps(user_id)` and `user_rolling_cap_reached(user_id) -> bool` (true when daily or monthly cap is set and spend >= cap). `budget_gate.budget_allows_dispatch` must call those helpers — no duplicated SpendPolicy reads. Do not import `apps.runner` from `apps.sessions`.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_budget.py backend/apps/runner/tests/test_budget_gate.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/sessions/services/budget.py backend/apps/sessions/tests/test_budget.py backend/apps/runner/budget_gate.py
git commit -m "feat: include algorithm usage in user spend, not agent spend"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Hourly aggregation for algorithm sessions

**Files:**
- Modify: `backend/apps/sessions/tasks.py`
- Modify: `backend/apps/sessions/tests/test_aggregation.py`

- [ ] **Step 1: Write failing aggregation test**

In `TestAggregateHourlyUsage`:

```python
def test_algorithm_llm_rolls_into_algorithm_bucket_not_chat_agent(self) -> None:
    agent = self._setup_agent('agg-chat', 'agg-chat-agent')
    user = agent.user
    chat = AgentSession.objects.create(
        agent=agent,
        agent_config=agent.current_config,
        status=AgentSessionStatus.DONE,
        trigger_type='trigger',
    )
    algo = AgentSession.objects.create(
        user=user,
        algorithm_id='chat_name',
        agent=None,
        agent_config=None,
        status=AgentSessionStatus.DONE,
        trigger_type='algorithm',
    )
    AgentSessionActivity.objects.create(
        session=algo,
        seq=1,
        revision=1,
        kind=AgentSessionActivityKind.LLM,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='chat_name',
        summary='title',
        details={'target_session_id': str(chat.id)},
        model='gpt-5.4-nano',
        input_tokens=10,
        output_tokens=4,
        cost_usd=Decimal('0.002000'),
    )
    aggregate_hourly_usage()
    self.assertFalse(HourlyUsage.objects.filter(agent_id=agent.id).exists())
    row = HourlyUsage.objects.get(user_id=user.id, algorithm_id='chat_name')
    self.assertEqual(row.cost_usd, Decimal('0.002000'))
    self.assertIsNone(row.agent_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py -v -k algorithm_llm`

Expected: FAIL (rollup still keys only `session__agent_id`, skipping or crashing on null agent)

- [ ] **Step 3: Implement dual rollup**

Keep `_replace_hourly_usage` for rows with `session__agent_id` not null (also set `user_id` from `session__user_id` on `update_or_create` defaults).

Add `_replace_algorithm_hourly_usage(cutoff, user_ids)`:

- `select_for_update` on `User` pks in `user_ids` (mutex analogous to Agent lock)
- Affected buckets: `(user_id, algorithm_id, hour)` from terminal llm/tool activities where `session__algorithm_id` is set
- `update_or_create` on `user_id`, `algorithm_id`, `hour`, `model` with `agent_id=None`

`aggregate_hourly_usage`:

```python
agent_ids = list(...values_list('session__agent_id', flat=True).exclude(session__agent_id=None).distinct())
user_ids = list(...filter(session__algorithm_id__isnull=False).values_list('session__user_id', flat=True).distinct())
if agent_ids:
    _replace_hourly_usage(cutoff, agent_ids)
if user_ids:
    _replace_algorithm_hourly_usage(cutoff, user_ids)
```

Never copy algorithm-session cost onto the chat agent’s `HourlyUsage` rows.

Generalize `_bucket_filter` with caller-supplied field names (`session__user_id` + `session__algorithm_id`, or `user_id` + `algorithm_id` for usage rows).

- [ ] **Step 4: Run aggregation tests**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_aggregation.py -v`

Expected: PASS (existing agent tests still create rows with `user` + `agent`)

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/sessions/tasks.py backend/apps/sessions/tests/test_aggregation.py
git commit -m "feat: roll algorithm activities into user+algorithm hourly buckets"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 5: `generate_chat_name` recorder + structured result (no session)

**Files:**
- Modify: `backend/libs/algorithms/chat_name.py`
- Modify: `backend/libs/algorithms/__init__.py`
- Modify: `backend/libs/algorithms/tests/test_chat_name.py`

- [ ] **Step 1: Write failing tests**

Replace string assertions with `.title`. Add:

```python
from libs.tools.activity import NoOpActivityRecorder


class TestGenerateChatNameRecorder(OTestCase):
    def test_disabled_does_not_open_a_session(self) -> None:
        recorder = NoOpActivityRecorder()
        result = generate_chat_name(
            'Hello there',
            config=ChatNameConfig(enabled=False),
            recorder=recorder,
        )
        self.assertEqual(result.title, 'Hello there')
        self.assertIsInstance(result, ChatNameResult)

    def test_repeat_provider_records_llm_when_recorder_provided(self) -> None:
        recorder = _FakeRecorder()
        result = generate_chat_name(
            'Summarize quarterly revenue',
            config=ChatNameConfig(provider='repeat', model='repeat'),
            recorder=recorder,
        )
        self.assertTrue(result.title)
        self.assertIn('llm', recorder.starts)
        self.assertTrue(recorder.completed)
```

Keep libs tests Django-free. Add this helper in `test_chat_name.py` (implement every `ActivityRecorder` method; unused ones can `raise AssertionError('unexpected')`):

```python
class _FakeRecorder:
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.completed = False

    def start(self, *, kind: str, name: str, summary: str, details=None, status='running'):
        del name, summary, details
        self.starts.append(kind)
        return ActivityRef(id=uuid4(), seq=len(self.starts), revision=1, kind=kind, status=status)

    def complete(self, activity_id, *, summary, details=None, status='succeeded', model=None,
                 input_tokens=None, output_tokens=None, cost_usd=None, latency_ms=None):
        del activity_id, summary, details, status, model, input_tokens, output_tokens, cost_usd, latency_ms
        self.completed = True
        return ActivityRef(id=uuid4(), seq=1, revision=1, kind='llm', status='succeeded')

    def fail(self, activity_id, **kwargs):
        return self.complete(activity_id, status='failed', **kwargs)

    def status_note(self, **kwargs):
        raise AssertionError('unexpected status_note')

    def span(self, **kwargs):
        raise AssertionError('unexpected span')

    def push_parent(self, activity_id):
        raise AssertionError('unexpected push_parent')

    def link_subagent(self, **kwargs):
        raise AssertionError('unexpected link_subagent')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/algorithms/tests/test_chat_name.py -v`

Expected: FAIL (`ChatNameResult` missing; return type still `str`)

- [ ] **Step 3: Implement**

```python
@dataclass
class ChatNameResult:
    """Title plus optional provider usage for the caller to persist."""

    title: str
    usage: Usage | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    model: str | None = None
    provider_failed: bool = False


def generate_chat_name(
    first_message: str,
    *,
    config: ChatNameConfig | None = None,
    llm: ProviderLLMConfig | None = None,
    recorder: ActivityRecorder | None = None,
) -> ChatNameResult:
    """Generate a short title; optionally record llm/span on the injected recorder.

    Does not create sessions. When ``recorder`` is omitted, behavior matches
    today's provider call with no persistence. When provided, nested ``start``
    uses the recorder's current parent (tool span or algorithm session root).
    """
```

On success: `recorder.start(kind='llm', name=cfg.model, ...)`, then `complete` with tokens/`cost_usd`/`latency_ms` from `StreamResult` + `provider.compute_cost_usd`. On provider failure: `fail` with any usage present, `provider_failed=True`, fallback title.

Keep Django out of this module. Update `__all__` with `ChatNameResult`.

Update existing tests to use `result.title`.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/libs/algorithms/tests/test_chat_name.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/libs/algorithms/chat_name.py backend/libs/algorithms/__init__.py backend/libs/algorithms/tests/test_chat_name.py
git commit -m "feat: record chat_name LLM usage via injected ActivityRecorder"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 6: Create algorithm sessions and wire `generate_session_name`

**Files:**
- Modify: `backend/apps/sessions/services/commands.py`
- Modify: `backend/apps/sessions/services/queries.py`
- Modify: `backend/apps/sessions/tasks.py`
- Modify: `backend/apps/sessions/tests/test_tasks.py`

- [ ] **Step 1: Write failing task tests**

Replace patches that treat `generate_chat_name` as returning `str` with `ChatNameResult(title=...)`.

```python
class TestGenerateSessionNameTask(OTransactionTestCase):
    @patch('apps.sessions.services.commands.publish_session_update')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(title='Password reset help', cost_usd=Decimal('0.001'), model='gpt-5.4-nano'),
    )
    def test_creates_algorithm_session_with_llm_and_names_both(self, mock_generate, _mock_publish):
        chat = make_test_session('name-task-agent')
        create_activity(
            chat,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
            summary='How do I reset my password?',
            details={'content': 'How do I reset my password?'},
        )
        generate_session_name.run(str(chat.id))
        chat.refresh_from_db()
        self.assertEqual(chat.name, 'Password reset help')
        self.assertFalse(
            AgentSessionActivity.objects.filter(session=chat, kind=AgentSessionActivityKind.LLM).exists()
        )
        algo = AgentSession.objects.get(algorithm_id='chat_name', user_id=chat.user_id)
        self.assertIsNone(algo.agent_id)
        self.assertEqual(algo.name, 'Password reset help')
        self.assertEqual(algo.status, AgentSessionStatus.DONE)
        root = AgentSessionActivity.objects.get(session=algo, parent_id=None)
        self.assertEqual(root.details.get('target_session_id'), str(chat.id))
        self.assertTrue(
            AgentSessionActivity.objects.filter(session=algo, kind=AgentSessionActivityKind.LLM).exists()
        )
        mock_generate.assert_called_once()
        self.assertIsNotNone(mock_generate.call_args.kwargs.get('recorder'))

    def test_skips_when_chat_already_named(self):
        # existing test: generate_chat_name must not be called; no new algorithm session

    def test_user_cap_skips_provider_and_still_writes_algorithm_session(self):
        # SpendPolicy daily 0 + existing HourlyUsage; patch generate_chat_name and
        # assert it was not called; chat gets fallback; algorithm session has failed llm
```

Because the task may call the real `generate_chat_name` unless patched, keep the patch for the happy path. For the cap test, set `SpendPolicy(daily_spend_limit_usd=Decimal('0.00'))` and `HourlyUsage` with `cost_usd=Decimal('1.00')` so `user_rolling_cap_reached` is true.

For retry idempotency:

```python
    def test_retry_does_not_call_provider_twice_for_same_chat(self):
        # First run with patch; second run with chat still named after first → not called
        # AND: first run creates algo session; if chat name write is simulated failed,
        # second run reuses session whose details.target_session_id matches
```

Implement reuse: `get_algorithm_session_for_target(user_id, algorithm_id, target_session_id)` in queries.py filtering activities `parent_id=None`, `details__target_session_id=str(uuid)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_tasks.py -v`

Expected: FAIL (task still only `update_session_name` on the chat)

- [ ] **Step 3: Implement commands + task**

`create_algorithm_session(*, user_id: int, algorithm_id: str) -> AgentSession`:

- `get_algorithm(algorithm_id)` or `ValidationError`
- Create `status=RUNNING`, `trigger_type=ALGORITHM`, `user_id=user_id`, `algorithm_id=...`, null agent/config/parent
- Do **not** dispatch `run_session`

`finish_algorithm_session(session, *, name: str | None)`: set `name` if provided via `update_session_name`, `set_session_status(..., DONE)`, set `ended_at`.

`generate_session_name` flow:

1. If `get_session_name(chat)`: return (no algorithm session)
2. If no first input text: return
3. Resolve `user_id` from `chat.user_id` (not only `chat.agent.user_id`)
4. Reuse existing algorithm session for `(user, chat_name, target=chat.id)` if present
5. Else `create_algorithm_session`
6. `create_activity` root span `kind=span`, `name='chat_name'`, `details={'target_session_id': str(chat.id)}` if not already present — **not** a `child_session_id`, **not** a column on `AgentSession`
7. If chat became named (race): `finish_algorithm_session` without provider; return
8. If `user_rolling_cap_reached(user_id)`: fallback title via `generate_chat_name(..., config=ChatNameConfig(enabled=False))` **without** provider; `create_activity`/`update` llm `status=failed`, summary `User rolling spend cap reached`; name algo + chat; finish; return
9. Else `BackendActivityRecorder(DjangoSessionBackend(algo))` + `push_parent(root_id)` then `generate_chat_name(..., recorder=recorder, llm=provider_config_from_spec(...))`
10. Name **algorithm session** then `update_session_name(chat.id, title)`
11. Finish algorithm session `DONE` (provider failure still DONE with failed llm — design)

If `generate_chat_name` is patched and does not call the recorder, the task must still persist an `llm` row from `ChatNameResult` (tokens/cost) so tests and aggregation see cost. Prefer: when the mock returns a result without recorder side effects, task writes llm from the result after the call. Simplest production path: always `start`/`complete` inside `generate_chat_name` when recorder is passed; tests that patch `generate_chat_name` should have the task write llm from `ChatNameResult` if no llm row exists:

```python
if not session.activities.filter(kind=LLM).exists():
    create_activity(..., kind=LLM, status=FAILED if result.provider_failed else SUCCEEDED, cost_usd=result.cost_usd, ...)
```

Existing `apps.sessions → apps.runner.llm_config` import may remain for `provider_config_from_spec`. Recorder import from `apps.runner.activity_recorder` is allowed only in `tasks.py` (already a Celery orchestrator). Do not import runner from `commands.py`.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_tasks.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/sessions/services/commands.py backend/apps/sessions/services/queries.py backend/apps/sessions/tasks.py backend/apps/sessions/tests/test_tasks.py
git commit -m "feat: run chat_name in an algorithm-owned session with traces"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 7: Dashboard Background, recents filter, algorithm detail

**Files:**
- Modify: `backend/apps/web/services/queries.py`
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/templates/web/dashboard.html`
- Create: `backend/templates/web/algorithm_detail.html`
- Create: `backend/apps/web/tests/test_background.py`
- Modify: `backend/apps/web/tests/test_session_dialog.py` only if recents assertions need `agent_id` filter

- [ ] **Step 1: Write failing web tests**

```python
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from apps.sessions.tests.base import make_test_session
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from libs.algorithms.registry import CHAT_NAME_ID

from olib.py.django.test.cases import OTransactionTestCase


class TestBackgroundDashboard(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.chat = make_test_session('bg-chat')
        self.user = get_user_model().objects.get(username='user-bg-chat')
        self.client.force_login(self.user)

    def test_dashboard_lists_chat_name_with_zero_runs(self) -> None:
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Background')
        self.assertContains(response, 'Chat name')
        self.assertContains(response, reverse('algorithm_detail', kwargs={'algorithm_id': CHAT_NAME_ID}))

    def test_recent_sessions_exclude_algorithm_sessions(self) -> None:
        algo = AgentSession.objects.create(
            user=self.user,
            algorithm_id=CHAT_NAME_ID,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.ALGORITHM,
        )
        response = self.client.get(reverse('dashboard'))
        ids = [row.id for row in response.context['sessions']]
        self.assertIn(self.chat.id, ids)
        self.assertNotIn(algo.id, ids)

    def test_algorithm_detail_lists_only_this_users_sessions(self) -> None:
        mine = AgentSession.objects.create(
            user=self.user,
            algorithm_id=CHAT_NAME_ID,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.ALGORITHM,
        )
        other_user = get_user_model().objects.create_user(username='bg-other', password='x')
        foreign = AgentSession.objects.create(
            user=other_user,
            algorithm_id=CHAT_NAME_ID,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.ALGORITHM,
        )
        response = self.client.get(reverse('algorithm_detail', kwargs={'algorithm_id': CHAT_NAME_ID}))
        self.assertEqual(response.status_code, 200)
        session_ids = [row.id for row in response.context['algorithm_sessions']]
        self.assertIn(mine.id, session_ids)
        self.assertNotIn(foreign.id, session_ids)
```

Unknown id → 404:

```python
    def test_unknown_algorithm_is_not_found(self) -> None:
        response = self.client.get(reverse('algorithm_detail', kwargs={'algorithm_id': 'nope'}))
        self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_background.py -v`

Expected: FAIL (no URL `algorithm_detail`, no Background heading)

- [ ] **Step 3: Implement queries, views, templates**

`get_dashboard_data`: after user filter, `sessions = sessions.filter(agent_id__isnull=False)` (Recent sessions agent-only).

Add dataclass `BackgroundAlgorithmRow` with `algorithm_id`, `display_name`, `session_count`, `daily_spend`, `monthly_spend`, `latest_created_at`.

`list_background_algorithms(user_id: int) -> list[BackgroundAlgorithmRow]`: iterate `list_algorithms()`, left-join counts via `AgentSession.objects.filter(user_id=..., algorithm_id=...)`.

`get_algorithm_detail_data(user_id, algorithm_id)`: 404 via `Http404` if `get_algorithm` is None; sessions `filter(user_id=user_id, algorithm_id=algorithm_id).order_by('-created_at')`; spend via `algorithm_*_spend`.

`get_owned_session`: `AgentSession.objects.select_related('agent', 'agent_config').get(pk=session_id, user_id=user_id)` so algorithm sessions are reachable.

`get_owned_direct_parent`: filter parent with `user_id=user_id` instead of `agent__user_id`.

`dashboard` view: pass `background_algorithms=list_background_algorithms(user_id)` when authenticated.

URL: `path('algorithms/<slug:algorithm_id>/', views.algorithm_detail, name='algorithm_detail')`.

Template `algorithm_detail.html`: usage daily/monthly for this algorithm; table of sessions linking to `session_detail`. No config, queues, delete, chatbox.

Dashboard: insert Background **below Agents, above Usage**. Each row: display name (link), daily spend, session count. Zero-run algorithms still listed.

Views call services only.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_background.py backend/apps/web/tests/test_session_dialog.py backend/apps/web/tests/test_usage_views.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/web backend/templates/web/dashboard.html backend/templates/web/algorithm_detail.html
git commit -m "feat: dashboard Background catalog and algorithm detail"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 8: Algorithm session page — traces, no composer

**Files:**
- Modify: `backend/apps/web/views.py` (`session_detail`, `session_chat`, pause/resume/abort)
- Modify: `backend/templates/web/layout/agent_frame_page.html`
- Modify: `backend/templates/web/session_detail.html`
- Modify: `backend/apps/web/tests/test_background.py` (add class)
- Modify: `backend/apps/web/services/queries.py` (`get_session_llm_label` for null `agent_config`)

- [ ] **Step 1: Write failing tests**

```python
class TestAlgorithmSessionPage(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = make_algorithm_session('algo-ui')
        self.user = self.session.user
        self.client.force_login(self.user)

    def test_shows_activity_panel_without_composer(self) -> None:
        response = self.client.get(reverse('session_detail', kwargs={'session_id': self.session.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="event-panel"')
        self.assertNotContains(response, 'Message the agent')
        self.assertNotContains(response, 'Back to agent')
        self.assertContains(response, 'Background')
        self.assertContains(
            response,
            reverse('algorithm_detail', kwargs={'algorithm_id': 'chat_name'}),
        )

    def test_chat_post_is_not_found(self) -> None:
        response = self.client.post(
            reverse('session_chat', kwargs={'session_id': self.session.id}),
            {'content': 'hi'},
        )
        self.assertEqual(response.status_code, 404)
```

Agent session pages must still include the composer (`test_session_detail_includes_shared_chatbox` in `test_chatbox_partial.py` remains green).

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_background.py::TestAlgorithmSessionPage backend/apps/web/tests/test_chatbox_partial.py -v`

Expected: FAIL (session_detail still requires `session.agent` and includes chatbox)

- [ ] **Step 3: Implement**

`get_session_llm_label`: if `session.agent_config_id` is None, return `session.algorithm_id` or `'—'`.

`session_detail`: `is_algorithm = session.algorithm_id is not None`. If algorithm: do **not** call `_chatbox_context`; context `show_composer=False`, `algorithm_id`, `algorithm_display_name` from registry; breadcrumb to algorithm detail — **no parent chat breadcrumb** (v1). If agent: `show_composer=True` as today.

`agent_frame_page.html`: wrap `{% include "web/partials/chatbox.html" %}` in `{% if show_composer %}`.

`session_detail.html` algorithm branch: title/header from algorithm display name; “Back to Background”; hide pause/resume/abort/new session/agent link.

`session_chat` / `session_pause` / `session_resume` / `session_abort`: if `session.algorithm_id`: `raise Http404`.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_background.py backend/apps/web/tests/test_chatbox_partial.py backend/apps/web/tests/test_session_dialog.py -v`

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/web backend/templates/web
git commit -m "feat: algorithm session page without chat composer"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 9: Architecture doc

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update documented boundaries** (no test; review in S_final)

In the app dependency table, change sessions to: `libs.algorithms` from **tasks and commands that start algorithm sessions**.

Add a short **Sessions and usage ownership** subsection:

- `AgentSession.user` is required; backfilled from `agent.user` for legacy agent rows
- XOR: agent+config **or** `algorithm_id` (registry in `libs.algorithms`); no Agent rows for algorithms
- No `target_session` field; optional `details.target_session_id` on the algorithm session root activity
- `HourlyUsage.user` required; agent buckets vs `(user, algorithm_id, hour, model)` buckets
- `user_*_spend` includes both; `agent_*_spend` is agent_id only
- Dashboard Background lists the registry; Recent sessions are agent-owned only
- Algorithm one-offs are `apps.sessions.tasks` (not `run_session`); in-tool algorithms use `ActivityRecorder` only

Update Celery bullet: `generate_session_name` creates an algorithm-owned session for traces/cost.

- [ ] **Step 2: Commit and sync**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: document algorithm session owner XOR and usage buckets"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 10: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Under `/ship`, fix actionable findings before PR; otherwise do not fix unless the user asks.

**Files:** (review only — no edits unless user/`/ship` requests fixes)

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

Read `superpowers/requesting-code-review`. Dispatch reviewer subagent with:

- `{DESCRIPTION}` — Background algorithms: required user FK, agent XOR algorithm session/usage owner, chat_name algorithm sessions with traces, user spend includes algorithms, recents agent-only, in-tool recorder creates no algorithm session
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-08-30-background-algorithms/2026-08-30-background-algorithms-design.md` and `docs/specs/2026-08-30-background-algorithms/2026-08-30-background-algorithms-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

1. Write `docs/specs/2026-08-30-background-algorithms/2026-08-30-background-algorithms-review.md`
2. One issue table per severity with columns: `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**
3. Summarize in chat

- [ ] **Step 5: Track feedback**

When fixing or rejecting: **Fixed** or **Rejected** (rationale in **Notes**)

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` unless `/ship` auto-opens the PR. Do **not** check boxes in `-revision.md` unless the user explicitly approves after review.

---

## Out of scope

- Algorithm rows in the Agents list, YAML agents, queues, chat composer, `run_session`
- Emitting rename work into the **chat** activity tree or `child_session_id` on the chat
- Mixing algorithm sessions into Recent sessions
- Counting algorithm spend against **agent** caps
- Manual “start algorithm” from the UI
- Per-algorithm spend limits
- Target session FK / column
- Django `Algorithm` model / Agent rows for algorithms
- New env vars for chat-name tuning
- Creating a background session when an algorithm is invoked with an in-tool `ActivityRecorder`

## References

- Design: `docs/specs/2026-08-30-background-algorithms/2026-08-30-background-algorithms-design.md`
- Architecture: `docs/ARCHITECTURE.md`
- Chat names: `docs/specs/2026-07-01-chat-names/`
- Activity trees: `docs/specs/2026-07-26-hierarchical-session-activities/`
