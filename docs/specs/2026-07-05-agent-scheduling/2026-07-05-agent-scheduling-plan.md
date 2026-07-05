# Agent scheduling Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **5 of 9** · Item: **Agent scheduling**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 below before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Wire **schedule** and **queue** triggers to Celery beat and session dispatch, with **`max_sessions`** concurrency on both kinds, immediate queue dispatch on **`put_item`**, and the deferred **`poll_active_sources`** platform beat.

**Architecture:** Extend **`TriggerSpec`** (no schema version bump). Add **`apps.runner.scheduling`** for dispatch logic, **`session_start.py`** for session creation without import cycles, and **`trigger_tasks.py`** for Celery entrypoints. Queue **`put_item`** enqueues runner dispatch via **`on_commit` + Celery task name** (no load-time `apps.queues` → `apps.runner` import).

**Tech Stack:** Django 5.2, Pydantic v2, Celery beat, **croniter**, existing `apps.queues.commands` (`take_item`, `put_item`).

**Branch:** `feat/2026-07-05-agent-scheduling`

**Design spec:** [`2026-07-05-agent-scheduling-design.md`](./2026-07-05-agent-scheduling-design.md)

**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-05-agent-scheduling || git checkout -b feat/2026-07-05-agent-scheduling
git branch --show-current   # must print feat/2026-07-05-agent-scheduling
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Ensure review template exists**

`docs/specs/2026-07-05-agent-scheduling/2026-07-05-agent-scheduling-revision.md` — leave review sections empty.

- [ ] **Step 0c: Commit plan (if uncommitted)**

```bash
git add docs/specs/2026-07-05-agent-scheduling/
git commit -m "docs(scheduling): add agent scheduling design and plan"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

Skip 0c if already committed on the feature branch.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …` (mutating: `./olib/scripts/orun …` for `py sync`, `makemigrations`)
- Gate after each stage: `./olib/scripts/orunr py test-all` (see `ai/commands/py-checks.md`)
- Django migrations: `./olib/scripts/orun django manage makemigrations agents` — never hand-write migration files
- Test bases: `OTestCase` / `OTransactionTestCase` from `olib.py.django.test.cases`
- Avoid parproc keywords in test names (`error`, `exception`, …)
- **Function documentation:** every new/changed function or method needs a brief docstring per [`AGENTS.md`](../../AGENTS.md)
- **No compatibility re-exports:** update imports to the canonical module; delete replaced files — no re-export shims
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final below)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts

---

## Target file map

```
backend/pyproject.toml                          # + croniter dependency

backend/libs/agent_spec/
  spec.py                                       # TriggerSpec: queue kind, max_sessions
  cron.py                                       # NEW — validate_cron_expression, cron_matches_minute

backend/apps/agents/
  models.py                                     # TriggerKind.QUEUE, Trigger.last_fired_at
  services/config_validation.py               # cron + trigger queue ref checks
  services/queries.py                           # TRIGGER_KINDS, SCHEMA_KEYS
  services/config_mutations.py                # add_trigger supports new fields
  tests/test_spec.py                            # TriggerSpec validation tests
  migrations/0005_….py                          # last_fired_at + kind choices (generated)

backend/apps/runner/
  session_start.py                              # NEW — StartSessionError, start_trigger_session
  start.py                                      # manual start delegates to session_start
  dispatch.py                                   # lazy-import run_session (break cycles)
  scheduling.py                                 # NEW — dispatch_schedule/queue + helpers
  trigger_tasks.py                              # NEW — Celery beat wrappers
  tests/test_scheduling.py                      # NEW — dispatch tests

backend/apps/queues/
  tasks.py                                      # + poll_active_sources, notify_item_available relay
  services/commands.py                          # put_item on_commit hook
  tests/test_tasks.py                           # poll_active_sources test
  tests/test_put_dispatch.py                    # NEW — immediate dispatch test

backend/apps/web/
  views.py                                      # import StartSessionError from session_start

backend/chief/
  celery.py                                     # beat schedule entries
  tasks.py                                      # import trigger_tasks module

backend/libs/agent_specs/examples/
  queue-echo.yaml                               # + queue trigger with max_sessions

docs/ARCHITECTURE.md                            # optional short scheduling paragraph
```

---

## S1 — Schema and cron helper

### Task 1: `croniter` dependency and `TriggerSpec` extensions

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/libs/agent_spec/cron.py`
- Modify: `backend/libs/agent_spec/spec.py`
- Test: `backend/apps/agents/tests/test_spec.py`

- [ ] **Step 1: Add dependency**

In `backend/pyproject.toml` dependencies list add:

```toml
"croniter==6.0.0",
```

Run: `./olib/scripts/orun py sync`

- [ ] **Step 2: Write failing schema tests**

Add to `backend/apps/agents/tests/test_spec.py`:

```python
class TestTriggerSpec(OTestCase):
    def test_queue_trigger_requires_queue_field(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'worker', 'kind': 'queue'}],
                    'queues': [{'id': 'inbox', 'sources': []}],
                }
            )

    def test_queue_trigger_must_reference_declared_queue(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'worker', 'kind': 'queue', 'queue': 'missing'}],
                    'queues': [{'id': 'inbox', 'sources': []}],
                }
            )

    def test_schedule_trigger_requires_cron(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'sweep', 'kind': 'schedule'}],
                }
            )

    def test_max_sessions_defaults_to_one(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [{'name': 'worker', 'kind': 'queue', 'queue': 'inbox'}],
                'queues': [{'id': 'inbox', 'sources': []}],
            }
        )
        self.assertEqual(spec.triggers[0].max_sessions, 1)
```

- [ ] **Step 3: Run tests to verify failure**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py::TestTriggerSpec -v`

Expected: FAIL (unknown kind `queue` or missing fields)

- [ ] **Step 4: Implement `cron.py`**

Create `backend/libs/agent_spec/cron.py`:

```python
from __future__ import annotations

from datetime import datetime

from croniter import croniter


def validate_cron_expression(expression: str) -> None:
    """Raise ValueError when expression is not a valid 5-field cron string."""
    if not croniter.is_valid(expression):
        raise ValueError(f'invalid cron expression: {expression!r}')


def cron_matches_minute(expression: str, when: datetime) -> bool:
    """Return whether expression is due at the start of when's minute (UTC)."""
    minute_start = when.replace(second=0, microsecond=0)
    return bool(croniter.match(expression, minute_start))
```

- [ ] **Step 5: Extend `TriggerSpec` in `spec.py`**

```python
class TriggerSpec(BaseModel):
    name: str
    kind: Literal['schedule', 'manual', 'agent', 'queue']
    cron: str | None = None
    queue: str | None = None
    max_sessions: int = Field(default=1, ge=1)

    @model_validator(mode='after')
    def _kind_specific_fields(self) -> TriggerSpec:
        if self.kind == 'schedule' and not self.cron:
            raise ValueError('cron is required when kind is schedule')
        if self.kind == 'queue' and not self.queue:
            raise ValueError('queue is required when kind is queue')
        return self
```

Add to `AgentConfigSpec`:

```python
    @model_validator(mode='after')
    def _trigger_queue_refs(self) -> AgentConfigSpec:
        queue_ids = {queue.id for queue in self.queues}
        for trigger in self.triggers:
            if trigger.kind == 'queue' and trigger.queue not in queue_ids:
                raise ValueError(
                    f"trigger {trigger.name!r} references unknown queue {trigger.queue!r}",
                )
        return self
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py::TestTriggerSpec -v`

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/libs/agent_spec/ backend/apps/agents/tests/test_spec.py uv.lock
git commit -m "feat(agent-spec): add queue trigger kind and max_sessions"
git fetch origin main && git rebase origin/main && git push
```

---

## S2 — Save-time validation and Trigger model

### Task 2: Config validation + `TriggerKind` + migration

**Files:**
- Modify: `backend/apps/agents/services/config_validation.py`
- Modify: `backend/apps/agents/models.py`
- Modify: `backend/apps/agents/services/queries.py`
- Modify: `backend/apps/agents/services/config_mutations.py`
- Test: `backend/apps/agents/tests/test_config_validation.py` (create if missing, or extend existing)

- [ ] **Step 1: Write failing cron validation test**

```python
def test_invalid_cron_rejected_in_yaml_validation(self) -> None:
    raw = """
schema_version: 1
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: hi
triggers:
  - name: sweep
    kind: schedule
    cron: not-a-cron
"""
    with self.assertRaises(ConfigValidationError) as ctx:
        validate_agent_config_yaml(raw)
    self.assertTrue(any('cron' in item.path for item in ctx.exception.errors))
```

- [ ] **Step 2: Implement `_validate_triggers` in `config_validation.py`**

Import `validate_cron_expression` from `libs.agent_spec.cron`. Validate cron for schedule triggers; validate queue id in `spec.queues`.

- [ ] **Step 3: Add `TriggerKind.QUEUE` and `Trigger.last_fired_at`**

In `models.py`:

```python
class TriggerKind(models.TextChoices):
    ...
    QUEUE = 'queue', 'Queue'

class Trigger(models.Model):
    ...
    last_fired_at = models.DateTimeField(null=True, blank=True)
```

Run: `./olib/scripts/orun django manage makemigrations agents --name trigger_scheduling_fields`

- [ ] **Step 4: Update config catalog**

In `queries.py`: add `'queue'` to `TRIGGER_KINDS`; add `triggers[].queue`, `triggers[].max_sessions` to `SCHEMA_KEYS`.

In `config_mutations.py` `add_trigger`: pass `queue=`, `max_sessions=int(mutation.get('max_sessions') or 1)`.

- [ ] **Step 5: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/ -v -k 'Trigger or cron or config_validation'`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/agents/
git commit -m "feat(agents): trigger scheduling fields and validation"
git fetch origin main && git rebase origin/main && git push
```

---

## S3 — Session start module (import-cycle safe)

### Task 3: `session_start.py` and manual start refactor

**Files:**
- Create: `backend/apps/runner/session_start.py`
- Modify: `backend/apps/runner/start.py`
- Modify: `backend/apps/runner/dispatch.py`
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/tests/test_agent_start_chat.py` (patch path if needed)

- [ ] **Step 1: Create `session_start.py`**

```python
class StartSessionError(Exception):
    """Agent is not ready to start a session from the requested trigger."""


def start_trigger_session(agent: Agent, trigger: Trigger) -> AgentSession:
    """Create a queued session bound to an active trigger on the agent's current config."""
    ...
```

Validate: agent has `current_config`; trigger belongs to agent; trigger on current config; trigger active.

- [ ] **Step 2: Refactor `start.py`**

- Import `StartSessionError`, `start_trigger_session` from `session_start`.
- `start_manual_session` finds manual trigger, calls `start_trigger_session`, then lazy-imports `push_chat_and_dispatch` from `dispatch`.

- [ ] **Step 3: Lazy-import `run_session` in `dispatch.py`**

Move `from apps.runner.tasks import run_session` inside `maybe_dispatch_session` to avoid pylint cyclic import with future `scheduling` → `dispatch` → `tasks`.

- [ ] **Step 4: Update `web/views.py`**

```python
from apps.runner.session_start import StartSessionError
from apps.runner.start import start_manual_session
```

- [ ] **Step 5: Run existing web + runner tests**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_agent_start_chat.py backend/apps/runner/tests/ -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/runner/ backend/apps/web/
git commit -m "refactor(runner): extract session_start for trigger dispatch"
git fetch origin main && git rebase origin/main && git push
```

---

## S4 — Scheduling core

### Task 4: `scheduling.py` helpers and constants

**Files:**
- Create: `backend/apps/runner/scheduling.py`
- Test: `backend/apps/runner/tests/test_scheduling.py`

- [ ] **Step 1: Write failing tests for helpers**

Test `active_session_count` (mock sessions), `queue_item_bootstrap_message` format, `_active_triggers` queryset filter (agent.current_config match).

- [ ] **Step 2: Implement `scheduling.py` skeleton**

Key exports:

```python
SCHEDULE_BOOTSTRAP = 'Scheduled run started. Execute your configured tasks.'

_ACTIVE_STATUSES = frozenset({QUEUED, RUNNING, PAUSED, WAITING})

def active_session_count(trigger: Trigger) -> int: ...

def queue_item_bootstrap_message(*, item_id: UUID, payload: dict) -> str: ...

def _active_triggers(*, kind: str) -> list[Trigger]:
    # Trigger.objects.filter(kind=kind, status=ACTIVE,
    #   agent__current_config_id=F('agent_config_id'))
```

Use lazy imports inside `dispatch_schedule_triggers` / `dispatch_queue_triggers` for `push_chat_and_dispatch` and `start_trigger_session`.

- [ ] **Step 3: Run helper tests — PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/apps/runner/scheduling.py backend/apps/runner/tests/test_scheduling.py
git commit -m "feat(runner): scheduling helpers and bootstrap messages"
git fetch origin main && git rebase origin/main && git push
```

---

## S5 — Schedule dispatch

### Task 5: `dispatch_schedule_triggers`

**Files:**
- Modify: `backend/apps/runner/scheduling.py`
- Modify: `backend/apps/runner/tests/test_scheduling.py`

- [ ] **Step 1: Write failing schedule dispatch tests**

Use `persist_agent_config` to create agent with schedule trigger `cron='0 * * * *'`. Patch `push_chat_and_dispatch`. Call `dispatch_schedule_triggers(now=datetime(2026, 7, 5, 14, 0, tzinfo=UTC))`. Assert one session, `last_fired_at` set, no refire same minute.

Add test: at `max_sessions` capacity → no new session, `last_fired_at` still updated.

- [ ] **Step 2: Implement `dispatch_schedule_triggers`**

Return `DispatchStats(schedule_sessions=N)`. Per design: cron match + last_fired_at minute dedup + max_sessions check.

- [ ] **Step 3: Run tests — PASS**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(runner): schedule trigger dispatch"
```

---

## S6 — Queue dispatch (beat path)

### Task 6: `dispatch_queue_triggers` and `dispatch_queue_triggers_for_queue`

**Files:**
- Modify: `backend/apps/runner/scheduling.py`
- Modify: `backend/apps/runner/tests/test_scheduling.py`

- [ ] **Step 1: Write failing queue dispatch tests**

- `put_item` + `dispatch_queue_triggers` → session created, item `taken`, bootstrap message contains `item_id`.
- `max_sessions=1` with existing active session → no second dispatch.
- `dispatch_queue_triggers_for_queue(queue.id)` only runs triggers for that queue.

- [ ] **Step 2: Implement queue dispatch**

Shared inner function `_fill_queue_trigger_slots(trigger, queue)` with while-loop take logic. Empty take → delete session.

- [ ] **Step 3: Run tests — PASS**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(runner): queue trigger dispatch with max_sessions"
```

---

## S7 — Celery tasks and beat

### Task 7: `trigger_tasks.py`, `poll_active_sources`, beat config

**Files:**
- Create: `backend/apps/runner/trigger_tasks.py`
- Modify: `backend/apps/queues/tasks.py`
- Modify: `backend/chief/celery.py`
- Modify: `backend/chief/tasks.py`
- Test: `backend/apps/queues/tests/test_tasks.py`

- [ ] **Step 1: Write failing `poll_active_sources` test**

Patch `poll_source.delay`. Create two active sources. Call task. Assert both enqueued.

- [ ] **Step 2: Implement tasks**

`trigger_tasks.py`:

```python
@shared_task(ignore_result=True)
def dispatch_schedule_triggers() -> None:
    from apps.runner.scheduling import dispatch_schedule_triggers as cmd
    cmd()

@shared_task(ignore_result=True)
def dispatch_queue_triggers() -> None:
    from apps.runner.scheduling import dispatch_queue_triggers as cmd
    cmd()

@shared_task(ignore_result=True)
def dispatch_queue_triggers_for_queue(queue_pk: str) -> None:
    from apps.runner.scheduling import dispatch_queue_triggers_for_queue as cmd
    cmd(queue_pk=queue_pk)
```

`queues/tasks.py`:

```python
@shared_task(ignore_result=True)
def poll_active_sources() -> None:
    for source_pk in Source.objects.filter(status=SourceStatus.ACTIVE).values_list('pk', flat=True):
        poll_source.delay(str(source_pk))
```

- [ ] **Step 3: Register beat in `chief/celery.py`**

Add entries per design (60s schedule, 15s queue, 300s poll). Import `apps.runner.trigger_tasks` in `chief/tasks.py`.

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(celery): scheduling beat tasks and poll_active_sources"
```

---

## S8 — Immediate dispatch on `put_item`

### Task 8: Put hook without load-time runner import

**Files:**
- Modify: `backend/apps/queues/services/commands.py`
- Create: `backend/apps/queues/tasks.py` relay (or inline send_task)
- Create: `backend/apps/queues/tests/test_put_dispatch.py`

- [ ] **Step 1: Write failing immediate dispatch test**

`put_item` with available item → assert Celery task enqueued (mock `send_task` or `dispatch_queue_triggers_for_queue.delay`).

Second test: slots full → item stays `available`, no session.

- [ ] **Step 2: Add hook to `put_item`**

After successful put, when item status is `AVAILABLE`:

```python
def _notify_queue_item_available(queue_id: UUID) -> None:
    from celery import current_app
    current_app.send_task(
        'apps.runner.trigger_tasks.dispatch_queue_triggers_for_queue',
        args=[str(queue_id)],
    )

# inside put_item, before return:
transaction.on_commit(lambda: _notify_queue_item_available(queue.id))
```

Fire on both newly created items and dedup return when existing row is still `available`.

- [ ] **Step 3: Run tests — PASS**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(queues): immediate queue dispatch on put_item"
```

---

## S9 — Example spec and docs

### Task 9: `queue-echo.yaml` and ARCHITECTURE

**Files:**
- Modify: `backend/libs/agent_specs/examples/queue-echo.yaml`
- Modify: `docs/ARCHITECTURE.md` (optional short paragraph under agent configuration)

- [ ] **Step 1: Update example YAML**

```yaml
triggers:
  - name: manual
    kind: manual
  - name: inbox-worker
    kind: queue
    queue: inbox
    max_sessions: 2
```

- [ ] **Step 2: Run example load test**

Run: `./olib/scripts/orunr py test backend/libs/agent_specs/tests/ -v`

- [ ] **Step 3: Add ARCHITECTURE paragraph** (2–4 sentences on schedule/queue triggers + beat)

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(scheduling): example spec and architecture note"
```

---

## S10 — Full verification

### Task 10: Regression gate

- [ ] **Step 1: Run full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: backend tests, lint, mypy pass (ignore unrelated olib snap test failure if present).

- [ ] **Step 2: Commit any fixups**

- [ ] **Step 3: Push**

```bash
git fetch origin main && git rebase origin/main && git push
```

---

## Out of scope (explicit)

- **`poll_sources`** on schedule triggers (deferred)
- **`agent` trigger dispatch**
- Config UI helpers for queue/schedule triggers
- Dedicated **`agent-runs` Celery queue**
- Validating credentials at ingest

---

## S_final — Code review (mandatory)

### Task 11: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using `requesting-code-review/code-reviewer.md`. Write findings to **`2026-07-05-agent-scheduling-review.md`**. Do not fix findings unless the user asks.

**Files:** (review only — no edits unless user requests fixes)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Dispatch reviewer with:

- `{DESCRIPTION}` — Agent scheduling: schedule + queue triggers, max_sessions, beat + immediate put dispatch, poll_active_sources
- `{PLAN_OR_REQUIREMENTS}` — this plan + design doc paths
- `{BASE_SHA}` / `{HEAD_SHA}` from Step 2

- [ ] **Step 4: Write `2026-07-05-agent-scheduling-review.md` and report in chat**

- [ ] **Step 5: Track feedback** — update Status to Fixed/Rejected when user responds

- [ ] **Step 6: Human handoff** — offer `superpowers/finishing-a-development-branch`

---

## References

- [Design](./2026-07-05-agent-scheduling-design.md)
- [Sources and queues plan](../2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-plan.md) (pattern reference)
- [Epic](../../epics/2026-07-03-inbox-cleanup.md)
