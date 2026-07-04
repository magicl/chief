# Sources and queues Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **3 of 9** · Item: **Sources and queues**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 below before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S10** (`superpowers/requesting-code-review`).

**Goal:** Deliver agent-scoped queues with optional nested sources, deduped items, full attempt history (`QueueItemAttempt`), materialization from optional `queues[]` in `AgentConfigSpec`, source adapter framework with a `test` adapter, stale-release beat task, and a gated `queue` tool (`put` / `take` / `complete` / `fail`).

**Architecture:** Pydantic schema in **`libs/agent_spec`** (extracted from `apps.agents`). **`apps.queues`** owns ORM + commands. **`apps.agents.materialize_agent_config`** orchestrates `sync_from_spec` inside `persist_agent_config`'s transaction. **`libs/sources`** holds Django-free adapters; poll tasks call into `apps.queues.commands`.

**Tech Stack:** Django 5.2, Pydantic v2, Celery beat, existing `libs/tools` registry + tool instance wiring.

**Branch:** `feat/2026-07-04-sources-and-queues`

**Design spec:** [`2026-07-04-sources-and-queues-design.md`](./2026-07-04-sources-and-queues-design.md)
**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-04-sources-and-queues || git checkout -b feat/2026-07-04-sources-and-queues
git branch --show-current   # must print feat/2026-07-04-sources-and-queues
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Ensure review template exists**

`docs/specs/2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-revision.md` — leave review sections empty.

- [ ] **Step 0c: Commit plan (if uncommitted)**

```bash
git add docs/specs/2026-07-04-sources-and-queues/
git commit -m "docs(queues): add sources and queues design and plan"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

Skip 0c if already committed on the feature branch.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr dev test-all` (or `py test-all` if faster iteration)
- Django migrations: `./olib/scripts/orunr django manage makemigrations queues` — never hand-write migration files
- Test bases: `OTestCase` / `OTransactionTestCase` from `olib.py.django.test.cases`
- Avoid parproc keywords in test names (`error`, `exception`, …)
- **Function documentation:** every new/changed function or method needs a brief docstring per [`AGENTS.md`](../../AGENTS.md) (purpose + non-obvious assumptions)
- **No compatibility re-exports:** update imports to the canonical module; delete replaced files — never leave re-export shims
- **Final task:** code review via **`superpowers/requesting-code-review`** (S10 below)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts
- **Payload cap:** reject `put` payloads &gt; **65536** bytes JSON-encoded (locked for v1)
- **Terminal item retention:** keep forever (no archive job in v1)

---

## Target file map

```
backend/libs/agent_spec/
  __init__.py                 # load_spec, load_spec_dict, AgentConfigSpec exports
  spec.py                     # AgentConfigSpec, QueueSpec, SourceSpec, ToolInstance, …
  exceptions.py
  registry.py                 # spec migration discovery (moved from apps/agents)
  migrations/
    __init__.py
    001_tool_instances.py     # moved unchanged

backend/apps/agents/
  spec.py                     # DELETE — use libs/agent_spec only
  materialize.py              # materialize_agent_config()
  ingest.py                   # call materialize after AgentConfig create
  spec_migrations/            # DELETE after move — or re-export shim one release
  tools_wiring.py             # + register QueueTool
  tool_wiring.py              # + session/agent context for queue tool
  tests/
    test_materialize.py       # NEW
    test_ingest.py              # + queues materialize case

backend/apps/queues/          # NEW Django app
  apps.py
  models.py                   # Queue, Source, QueueItem, QueueItemAttempt
  admin.py
  releasable.py               # is_session_releasable()
  services/
    queries.py
    commands.py               # put/take/complete/fail/release/sync_from_spec
  tasks.py                    # poll_source, poll_active_sources, release_stale_items
  tests/
    test_commands.py
    test_queries.py
    test_sync_from_spec.py
    test_release.py
    test_tasks.py
  migrations/

backend/libs/sources/
  __init__.py
  base.py                     # SourceAdapter protocol, PutItemCallback type
  registry.py
  adapters/
    __init__.py
    test.py                   # test adapter

backend/libs/tools/
  queue.py                    # QueueTool with bind(context)

backend/apps/runner/
  loop.py                     # build_bound_tools(..., session_id=, agent_id=)

backend/chief/
  settings.py                 # INSTALLED_APPS += apps.queues
  celery_beat.py or settings   # beat schedule for release_stale_items (see S7)

backend/apps/queues/management/commands/
  poll_source.py
  queue_item_attempts.py      # CLI debug: list attempts for item
```

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Queue ownership | **Agent-scoped**; `queues[]` optional on spec — **no schema_version bump** |
| Source placement | Nested **`queues[].sources`** |
| Materialization | **`apps.agents.materialize_agent_config`** → **`queues.sync_from_spec`** |
| Attempt history | **`QueueItemAttempt`** row on every **take** |
| Cross-agent feed | **`queue.put`** with `owner_agent` + `queue` args (same user) |
| Take atomicity | **`SELECT FOR UPDATE SKIP LOCKED`** |
| Max attempts exceeded on take | Skip to next item; mark **`exhausted`** if would exceed |
| Payload limit | **64 KiB** JSON |
| Queue trigger / dispatch | **Spec 5** — not here |
| Gmail adapter | **Spec 6** — ship **`test`** adapter only |

---

## S1 — Extract `libs/agent_spec`

Move schema + spec migrations out of `apps.agents` without behavior change.

### Task 1: Create `libs/agent_spec` package

**Files:**
- Create: `backend/libs/agent_spec/` (move from `apps/agents/spec.py`, `spec_migrations/`)
- Modify: all imports → `libs.agent_spec` (canonical path only)
- Delete: `backend/apps/agents/spec.py`, `backend/apps/agents/spec_migrations/`
- Test: existing `apps/agents/tests/test_spec*.py` still pass

- [ ] **Step 1: Move modules**

Move:
- `apps/agents/spec.py` → `libs/agent_spec/spec.py`
- `apps/agents/spec_migrations/*` → `libs/agent_spec/migrations/` + `registry.py`, `exceptions.py`
- Consolidate loader in `libs/agent_spec/__init__.py` (`load_spec`, `load_spec_dict`)

Update all imports to `libs.agent_spec` — **do not** leave a re-export shim at `apps/agents/spec.py`.

- [ ] **Step 2: Delete old modules**

Remove `apps/agents/spec.py` and `apps/agents/spec_migrations/` after imports are updated.

- [ ] **Step 3: Run tests**

```bash
./olib/scripts/orunr django manage test apps.agents.tests.test_spec apps.agents.tests.test_spec_migrations -v 0
```

- [ ] **Step 4: Commit**

```bash
git add backend/libs/agent_spec backend/apps/agents
git commit -m "refactor(agent_spec): extract schema and migrations to libs/agent_spec"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 2: Add optional `queues[]` to schema

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Test: `backend/apps/agents/tests/test_spec.py`

- [ ] **Step 1: Write failing tests**

```python
class TestQueueSpec(OTestCase):
    def test_queues_optional_defaults_empty(self) -> None:
        spec = AgentConfigSpec.model_validate(MINIMAL_SPEC_DICT)
        self.assertEqual(spec.queues, [])

    def test_queue_with_nested_sources(self) -> None:
        spec = AgentConfigSpec.model_validate({
            **MINIMAL_SPEC_DICT,
            'queues': [{
                'id': 'inbox',
                'sources': [{'id': 'gmail-a', 'type': 'test', 'config': {'prefix': 'x'}}],
            }],
        })
        self.assertEqual(spec.queues[0].id, 'inbox')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'test')
```

Use `_INSTANCE_ID_RE` for queue/source `id` fields (same pattern as tool instances).

- [ ] **Step 2: Add pydantic models**

```python
class SourceSpec(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str  # adapter_type in DB
    credential_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

class QueueSpec(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    max_attempts: int = 3
    min_hold_seconds: int = 60
    early_release_seconds: int = 300
    long_hold_seconds: int = 3600
    sources: list[SourceSpec] = []

class AgentConfigSpec(BaseModel):
    ...
    queues: list[QueueSpec] = []
```

**Do not bump `AGENT_CONFIG_SPEC_VERSION`.**

- [ ] **Step 3: Run tests + commit**

```bash
git commit -m "feat(agent_spec): add optional queues and nested sources to schema"
```

---

## S2 — `apps.queues` models

### Task 3: Django app skeleton + models

**Files:**
- Create: `backend/apps/queues/` (app config, models, empty services)
- Modify: `backend/chief/settings.py` — add `'apps.queues'`

- [ ] **Step 1: Write failing model tests**

```python
# backend/apps/queues/tests/test_models.py
class TestQueueModels(OTransactionTestCase):
    def test_queue_unique_per_agent_queue_id(self) -> None:
        ...
```

- [ ] **Step 2: Implement models**

Enums: `QueueItemStatus`, `QueueItemAttemptOutcome`, `SourceStatus`.

Key constraints:
- `UniqueConstraint(fields=['agent', 'queue_id'])` on `Queue`
- `UniqueConstraint(fields=['queue', 'source_id'])` on `Source`
- `UniqueConstraint(fields=['source', 'external_id'])` on `QueueItem` (where source not null)
- FK `QueueItem.taken_by_session` → `sessions.AgentSession`
- FK `QueueItemAttempt.session` → `sessions.AgentSession`

- [ ] **Step 3: makemigrations + migrate**

```bash
./olib/scripts/orunr django manage makemigrations queues
./olib/scripts/orunr django manage migrate
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(queues): add Queue, Source, QueueItem, QueueItemAttempt models"
```

---

## S3 — Commands: put / take / complete / fail

### Task 4: `put_item` + dedup

**Files:**
- Create: `backend/apps/queues/services/commands.py`, `queries.py`, `exceptions.py`
- Test: `backend/apps/queues/tests/test_commands.py`

- [ ] **Step 1: Failing tests** — create, dedup, terminal idempotent, payload size reject

- [ ] **Step 2: Implement `put_item`**

Validate payload JSON size ≤ 65536 bytes.

- [ ] **Step 3: Commit chunk**

```bash
git commit -m "feat(queues): add put_item with dedup and payload limit"
```

---

### Task 5: `take_item` + `QueueItemAttempt`

- [ ] **Step 1: Failing tests**

- take creates item + attempt with `in_progress`
- second take on empty queue returns None
- attempt_number matches `attempt_count`
- concurrent take: only one session gets item (`OTransactionTestCase` + two sequential takes)

- [ ] **Step 2: Implement atomic take**

Use `select_for_update(skip_locked=True)` on oldest `available` item.

On take: increment `attempt_count`, set `taken_*`, create `QueueItemAttempt`.

If increment would exceed `max_attempts`: mark item `exhausted`, do not assign session, try next item.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(queues): atomic take_item with QueueItemAttempt"
```

---

### Task 6: `complete_item` / `fail_item`

- [ ] **Step 1: Tests** — taker-only, closes attempt with `completed` / `failed`

- [ ] **Step 2: Implement**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(queues): complete_item and fail_item with attempt closure"
```

---

## S4 — Queries + stale release

### Task 7: Queries

**Files:**
- `backend/apps/queues/services/queries.py`
- `backend/apps/queues/tests/test_queries.py`

- [ ] **Step 1: Implement** `get_queue`, `list_queues`, `get_item`, `list_queue_items`, **`list_attempts_for_item`**

- [ ] **Step 2: Tests + commit**

```bash
git commit -m "feat(queues): add queue queries including attempt history"
```

---

### Task 8: `release_stale_items`

**Files:**
- Create: `backend/apps/queues/releasable.py`
- Modify: `commands.py`

- [ ] **Step 1: Implement `is_session_releasable`**

```python
def is_session_releasable(session: AgentSession) -> bool:
    return session.status in {AgentSessionStatus.DONE, AgentSessionStatus.WAITING} or session.ended_at is not None
```

- [ ] **Step 2: Failing tests for release**

Fixtures with `freeze_time` or explicit `taken_at` backdating:
- min_hold prevents early release
- early release when session done + held long enough → `available`, attempt `released`
- at max_attempts → `exhausted`, attempt `exhausted`
- long_hold releases even if session still running

- [ ] **Step 3: Implement `release_stale_items`**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(queues): stale item release with attempt outcomes"
```

---

## S5 — Materialization

### Task 9: `sync_from_spec`

**Files:**
- `backend/apps/queues/services/commands.py` — `sync_from_spec(agent, config, queues: list[QueueSpec])`
- `backend/apps/queues/tests/test_sync_from_spec.py`

- [ ] **Step 1: Tests**

- empty `queues` → no-op
- creates `Queue` + nested `Source` rows by stable ids
- re-sync updates `max_attempts`, adds/removes sources, sets `agent_config` FK
- removes sources absent from spec (disable or delete — **delete** orphaned sources with no items, else disable)
- validates adapter via `libs.sources.registry.get_adapter(type).validate_config`

- [ ] **Step 2: Implement `sync_from_spec`**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(queues): sync_from_spec materializes queues and sources from agent spec"
```

---

### Task 10: `materialize_agent_config` in agents

**Files:**
- Create: `backend/apps/agents/materialize.py`
- Modify: `backend/apps/agents/ingest.py`
- Test: `backend/apps/agents/tests/test_materialize.py`, extend `test_ingest.py`

- [ ] **Step 1: Extract trigger creation from `persist_agent_config` into materialize**

```python
# backend/apps/agents/materialize.py
def materialize_agent_config(agent: Agent, config: AgentConfig, spec: AgentConfigSpec) -> None:
    _sync_triggers(agent, config, spec.triggers)
    from apps.queues.services import commands as queue_commands
    queue_commands.sync_from_spec(agent, config, spec.queues)
```

- [ ] **Step 2: Call from `persist_agent_config`** after `AgentConfig.objects.create`

- [ ] **Step 3: Integration test** — persist spec with `queues:[{id: inbox, sources:[…]}]` → Queue row exists

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(agents): materialize queues from spec on config persist"
```

---

## S6 — Source adapters

### Task 11: `libs/sources` framework + test adapter

**Files:**
- Create: `backend/libs/sources/`
- Test: `backend/libs/sources/tests/test_registry.py`, `test_test_adapter.py`

- [ ] **Step 1: Protocol + registry** (`@functools.cache` discovery of `adapters/*.py`)

- [ ] **Step 2: Test adapter** — `validate_config`, `poll` enqueues `batch_size` items with `{prefix}-{n}` external_ids via callback

- [ ] **Step 3: Register in `apps/queues/apps.py` ready()** or import side-effect in `libs/sources/adapters/__init__.py`

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(sources): adapter registry and test source adapter"
```

---

### Task 12: Poll task

**Files:**
- `backend/apps/queues/tasks.py`
- `backend/apps/queues/management/commands/poll_source.py`
- Test: `backend/apps/queues/tests/test_tasks.py`

- [ ] **Step 1: `poll_source(source_pk)`** — load Source, resolve adapter, build `put_item` partial, optional `make_secret_supplier` for `credential_ref`, update `last_polled_at`

- [ ] **Step 2: Management command** `poll_source --agent-id --queue-id --source-id` (or UUIDs)

- [ ] **Step 3: Tests** with test adapter → items in queue

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(queues): poll_source task and management command"
```

---

### Task 13: Celery beat — `release_stale_items`

- [ ] **Step 1: Shared task** wrapping `commands.release_stale_items()`

- [ ] **Step 2: Register beat schedule** (every 2 minutes) — follow project Celery beat pattern (check `olib` django celery init or chief settings for where beat lives; add schedule entry)

- [ ] **Step 3: Test** command/task invokes release (mock time or backdated rows)

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(queues): celery beat task for stale item release"
```

Defer **`poll_active_sources`** beat to spec 5; manual/command poll is enough for v1.

---

## S7 — Queue tool + wiring

### Task 14: `libs/tools/queue.py`

**Files:**
- Create: `backend/libs/tools/queue.py`
- Test: `backend/libs/tools/tests/test_queue_tool.py` (create dir if needed)

- [ ] **Step 1: `QueueTool` with `bind(*, user_id, agent_id, session_id)`** returning bound invoke

Functions (LLM-visible args):
- `put(owner_agent: str, queue: str, payload: dict, external_id: str | None = None)` — resolve target agent by identifier + user; default owner is session's agent for take ops
- `take(queue: str)` — queue on **session's agent**
- `complete(item_id: str)`
- `fail(item_id: str, reason: str = '')`

Handlers delegate to `apps.queues.services.commands` (import inside handler to avoid circular imports at module load).

- [ ] **Step 2: Register in `wire_tools()`**

- [ ] **Step 3: Unit tests** with mocked commands module

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(tools): add queue tool with put take complete fail"
```

---

### Task 15: Tool wiring + runner session context

**Files:**
- Modify: `backend/apps/agents/tool_wiring.py`
- Modify: `backend/apps/runner/loop.py`
- Test: `backend/apps/agents/tests/test_tool_wiring.py`, integration test

- [ ] **Step 1: Extend `build_bound_tools`**

```python
def build_bound_tools(
    instances: list[ToolInstance],
    *,
    user_id: int | None,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
) -> dict[str, BoundToolInstance]:
```

For `inst.type == 'queue'`: use `QueueTool.bind(user_id=..., agent_id=..., session_id=...)`.

- [ ] **Step 2: Pass ids from `SessionRunner.for_session`**

```python
self.bound_tools = build_bound_tools(
    self.config_spec.tools,
    user_id=self.backend.user_id,
    agent_id=session.agent_id,
    session_id=session.id,
)
```

- [ ] **Step 3: Round-trip test** — agent with queue tool instance + materialized queue; session take → complete via bound invoke

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(agents): wire queue tool with session and agent context"
```

---

## S8 — Admin & operability

### Task 16: Django admin

**Files:**
- `backend/apps/queues/admin.py`

- [ ] **Step 1: Register** `Queue`, `Source`, `QueueItem` with inline **`QueueItemAttempt`** (readonly, all sessions)

- [ ] **Step 2: Management command** `queue_item_attempts <item_uuid>` prints attempt table for CLI debug

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(queues): admin and queue_item_attempts management command"
```

---

## S9 — Docs & regression

### Task 17: Final gate

- [ ] **Run full suite**

```bash
./olib/scripts/orunr dev test-all
```

- [ ] **Update epic** — check off spec 3 when implementation reviewed:

```markdown
- [x] 3. Sources and queues — [spec](...) · [plan](...)
```

- [ ] **Verify** `docs/ARCHITECTURE.md` matches implementation (already updated during design)

- [ ] **Commit any doc/epic checkbox** (human review before checking epic box — implementer may only check epic after user review per project policy; leave unchecked until review)

```bash
git commit -m "docs(queues): mark spec 3 implementation complete in epic"
```

---

## S10 — Code review (mandatory)

### Task 18: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using `requesting-code-review/code-reviewer.md`. Review branch `feat/2026-07-04-sources-and-queues` against the design spec. Write **[`-review.md`](./2026-07-04-sources-and-queues-review.md)**; summarize in chat. Do not fix unless the user asks.

**Files:** (review only)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr dev test-all
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

- `{DESCRIPTION}` — Sources and queues: agent-scoped queues, nested sources, attempt history, materialization, test adapter, queue tool, stale release
- `{PLAN_OR_REQUIREMENTS}` — [`2026-07-04-sources-and-queues-design.md`](./2026-07-04-sources-and-queues-design.md) and this plan; focus on put/dedup concurrency, sync_from_spec orphan cleanup, take_item session ownership, poll_source error handling, release_stale_items transaction scope
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Read `superpowers/requesting-code-review` and **`review-file-template.md`**.

1. Write [`2026-07-04-sources-and-queues-review.md`](./2026-07-04-sources-and-queues-review.md).
2. Issue tables: `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**.
3. Summarize the same content in chat.

Stop unless the user asks to fix issues.

- [ ] **Step 5: Track feedback**

Update **Status** in `*-review.md` as the user gives feedback: **Fixed** (implemented) or **Rejected** (declined; rationale in **Notes**).

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. Do **not** check epic spec 3 or `-revision.md` boxes unless the user explicitly approves after review.

---

## Testing matrix (acceptance)

| Area | Command / location |
|------|-------------------|
| Spec optional queues | `apps.agents.tests.test_spec` |
| Spec migration regression | `apps.agents.tests.test_spec_migrations` |
| put/dedup/take/complete/fail | `apps.queues.tests.test_commands` |
| Attempt history | `apps.queues.tests.test_queries` |
| Stale release | `apps.queues.tests.test_release` |
| sync_from_spec | `apps.queues.tests.test_sync_from_spec` |
| Poll task | `apps.queues.tests.test_tasks` |
| Queue tool wiring | `apps.agents.tests.test_tool_wiring` |
| Full gate | `orun dev test-all` |

---

## Out of scope (explicit)

- `TriggerSpec` `kind: queue`, `max_workers`, auto session dispatch (**spec 5**)
- Gmail adapter (**spec 6**)
- Dashboard UI for queues (**spec 4**)
- Validating `credential_ref` exists at ingest (**runtime** only)
- `poll_active_sources` Celery beat (**spec 5** or ops follow-up)

---

## References

- [Design](./2026-07-04-sources-and-queues-design.md)
- [Architecture — Agent configuration & queues](../../ARCHITECTURE.md)
- [Agent config schema plan](../2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-plan.md) (pattern reference)
- [Epic](../../epics/2026-07-03-inbox-cleanup.md)
