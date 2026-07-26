# Hierarchical Session Activities Implementation Plan

Epic: [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md) · Spec **2 of 3** · Item: **Hierarchical session activities**

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-07-26-hierarchical-session-activities/2026-07-26-hierarchical-session-activities-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Replace the flat `AgentSessionEvent` log with a typed, nestable `AgentSessionActivity` tree (including unified tool lifecycle, LLM/span/status containers, and linked sub-agent sessions), migrate historical rows, and keep provider rebuild / usage / SSE correct.

**Architecture:** Persistence stays in `apps.sessions` (models + services). The runner owns an activity recorder on `SessionBackend` / `ToolContext` (Django-free protocol in `libs.tools`). SSE publishes idempotent activity upserts on the existing session Redis channel. Sub-agent starts are one atomic sessions command that creates the parent `subagent` activity, child `AgentSession(parent_session=…)`, and schedules via existing `apps.runner.dispatch`.

**Tech Stack:** Django 5.x, PostgreSQL, Redis pub/sub SSE, Celery, `OTestCase` / `OTransactionTestCase`

**Branch:** `feat/2026-07-26-hierarchical-session-activities`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (or scoped tests while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-07-26-hierarchical-session-activities` from the plan, and after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims (`events.py` → delete after call sites move; do not leave `AgentSessionEvent = AgentSessionActivity`)
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`ai/commands/py-checks.md`)
- **CLI stdout:** capture with `self.captureStdout()` and assert; do not leave `click.echo` status lines uncaptured (`ai/commands/py-checks.md`)
- **Django migrations:** schema via `./olib/scripts/orunr django manage makemigrations …`; data migration via `makemigrations --empty` then fill `RunPython` — never hand-author a full schema migration file from scratch
- **Final task:** code review via **`superpowers/requesting-code-review`** (see mandatory **S_final** section below)
- Test naming: avoid keywords `error`, `exception`, `warning`, `deprecated` in test names (use failure / raises / advisory / legacy)
- Layering: views → services → ORM; runner backends call session **services**, not raw activity ORM writes; `libs.*` never imports `apps.*`
- Kinds/statuses use the design’s lowercase vocabulary (`input`, `output`, `tool`, `llm`, `span`, `status`, `subagent`, `failure`, `restart`; `pending`/`running`/`succeeded`/`failed`/`cancelled`)

---

## File map

| Path | Action | Responsibility |
|------|--------|----------------|
| `backend/apps/sessions/models.py` | Modify | `AgentSession.parent_session`; replace `AgentSessionEvent*` with `AgentSessionActivity*` |
| `backend/apps/sessions/migrations/0006_*.py` | Generate | Schema: parent FK + activity table (or rename + new columns) |
| `backend/apps/sessions/migrations/0007_*.py` | Generate empty + fill | Data: map events → activities; pair tools; drop orphans into `status` |
| `backend/apps/sessions/migrations/0008_*.py` | Generate | Drop legacy event model/table if still present after rename path |
| `backend/apps/sessions/activities.py` | Create | Seq allocation, create/update with parent validation + revision locking |
| `backend/apps/sessions/events.py` | Delete | Replaced by `activities.py` + services (no shim) |
| `backend/apps/sessions/services/commands.py` | Modify | Terminal/lifecycle create+update; `record_input`; `start_linked_child_session`; reconcile parent `subagent` |
| `backend/apps/sessions/services/queries.py` | Modify | Activities list, parent breadcrumbs, child sessions, input helpers on activity kinds |
| `backend/apps/sessions/notify.py` | Modify | `session_activity` upsert channel; keep `session_update` |
| `backend/apps/sessions/rebuild.py` | Modify | Rebuild from activities; expand unified `tool` into call+result messages |
| `backend/apps/sessions/tasks.py` | Modify | Aggregate terminal `llm` + `tool` activities |
| `backend/apps/sessions/admin.py` | Modify | Inline/admin for activities |
| `backend/apps/sessions/tests/test_activities.py` | Create | Model/service hierarchy, revision, terminal immutability |
| `backend/apps/sessions/tests/test_activity_migration.py` | Create | Import and run data-migration forwards on seeded legacy rows |
| `backend/apps/sessions/tests/test_events.py` | Delete/rename | Replaced by activity tests |
| `backend/apps/sessions/tests/test_rebuild.py` | Modify | Unified tool + ignore containers |
| `backend/apps/sessions/tests/test_aggregation.py` | Modify | Count `llm`/`tool` terminal rows |
| `backend/apps/sessions/tests/test_services.py` | Modify | `record_input` + publish upsert |
| `backend/libs/tools/activity.py` | Create | `ActivityRecorder` protocol, handles, `NoOpActivityRecorder` |
| `backend/libs/tools/context.py` | Modify | Optional `recorder` field (default no-op) |
| `backend/libs/tools/__init__.py` | Modify | Export recorder types |
| `backend/apps/runner/backends/base.py` | Modify | `RecordedActivity`; create/update/list/publish APIs |
| `backend/apps/runner/backends/django.py` | Modify | Delegate to session services + publish upserts |
| `backend/apps/runner/backends/memory.py` | Modify | In-memory activity tree + scope stack parity |
| `backend/apps/runner/activity_recorder.py` | Create | Backend-backed recorder with parent scope stack |
| `backend/apps/runner/loop.py` | Modify | LLM/tool lifecycle recording; nest children under current scope |
| `backend/apps/runner/hooks.py` | Modify | `on_activity_created` / `on_activity_updated` (+ keep tool/generate hooks) |
| `backend/apps/runner/usecases/observability.py` | Modify | Adapt eval JSONL to activity hooks |
| `backend/apps/runner/session_start.py` / `start.py` | Modify | Optional parent linkage args; or call sessions command |
| `backend/apps/runner/dispatch.py` | Unchanged API | Still `run_session.delay` / `push_chat_and_dispatch` |
| `backend/apps/web/views.py` | Modify | SSE replay/tail uses activities + upsert dedupe by id/revision |
| `backend/templates/web/session_detail.html` | Modify | Minimal flat upsert consumer (no tree UI — that is spec 3) |
| `backend/apps/web/tests/test_sse.py` | Modify | Upsert replay + revision |
| `backend/apps/runner/tests/test_loop.py` | Modify | Unified tool; LLM parent of output/tool |
| `backend/apps/runner/tests/test_hooks.py` | Modify | Activity hooks |
| `backend/apps/runner/tests/test_activity_recorder.py` | Create | Nested scopes + memory/django parity |
| `backend/apps/runner/tests/test_child_session.py` | Create | Linked sub-agent start + status reconcile |
| `backend/apps/runner/tests/usecases/test_inbox_functional.py` | Modify | Assert `tool` activities instead of `TOOL_CALL`/`TOOL_RESULT` |

---

### Task 1: Models — `parent_session` + `AgentSessionActivity`

**Files:**
- Modify: `backend/apps/sessions/models.py`
- Test: `backend/apps/sessions/tests/test_activities.py` (create)
- Generate: `backend/apps/sessions/migrations/0006_*.py` via Django tooling

- [ ] **Step 1: Write failing model tests**

```python
# backend/apps/sessions/tests/test_activities.py
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.tests.base import make_test_session
from django.db import IntegrityError

from olib.py.django.test.cases import OTransactionTestCase


class TestAgentSessionAncestry(OTransactionTestCase):
    def test_root_session_has_null_parent(self) -> None:
        session = make_test_session('root-act')
        self.assertIsNone(session.parent_session_id)

    def test_child_session_points_to_parent(self) -> None:
        parent = make_test_session('parent-act')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        self.assertEqual(child.parent_session_id, parent.id)
        self.assertEqual(list(parent.child_sessions.all()), [child])


class TestAgentSessionActivityBasics(OTransactionTestCase):
    def test_activity_defaults_and_seq_unique(self) -> None:
        session = make_test_session('act-basic')
        row = AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
            summary='hi',
            details={'content': 'hi'},
            revision=1,
        )
        self.assertEqual(row.revision, 1)
        self.assertIsNone(row.parent_id)
        with self.assertRaises(IntegrityError):
            AgentSessionActivity.objects.create(
                session=session,
                seq=1,
                kind=AgentSessionActivityKind.OUTPUT,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='output',
                summary='x',
                details={},
                revision=1,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_activities.py -v`

Expected: FAIL — `AgentSessionActivity` / `parent_session` undefined (or import failure)

- [ ] **Step 3: Implement models**

In `backend/apps/sessions/models.py`:

1. Add on `AgentSession`:

```python
parent_session = models.ForeignKey(
    'self',
    null=True,
    blank=True,
    on_delete=models.CASCADE,
    related_name='child_sessions',
)
# Meta.indexes: add models.Index(fields=['parent_session'])
```

2. Replace `AgentSessionEventKind` / `AgentSessionEvent` with:

```python
class AgentSessionActivityKind(models.TextChoices):
    INPUT = 'input', 'Input'
    OUTPUT = 'output', 'Output'
    TOOL = 'tool', 'Tool'
    LLM = 'llm', 'LLM'
    SPAN = 'span', 'Span'
    STATUS = 'status', 'Status'
    SUBAGENT = 'subagent', 'Sub-agent'
    FAILURE = 'failure', 'Failure'
    RESTART = 'restart', 'Restart'


class AgentSessionActivityStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


class AgentSessionActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='activities')
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE, related_name='children'
    )
    seq = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=32, choices=AgentSessionActivityKind.choices)
    status = models.CharField(max_length=32, choices=AgentSessionActivityStatus.choices)
    name = models.CharField(max_length=255)
    summary = models.CharField(max_length=512, blank=True, default='')
    details = models.JSONField(default=dict)
    model = models.CharField(max_length=255, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    child_session = models.OneToOneField(
        AgentSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parent_activity',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['session', 'seq'], name='sessions_activity_session_seq_uniq'),
        ]
        indexes = [
            models.Index(fields=['session', 'seq']),
            models.Index(fields=['session', 'created_at']),
            models.Index(fields=['parent']),
        ]
        ordering = ['seq']

    def to_stream_dict(self) -> dict[str, Any]:
        """Serialize full activity for SSE upsert payloads."""
        return {
            'id': str(self.id),
            'session_id': str(self.session_id),
            'parent_id': str(self.parent_id) if self.parent_id else None,
            'seq': self.seq,
            'revision': self.revision,
            'kind': self.kind,
            'status': self.status,
            'name': self.name,
            'summary': self.summary,
            'details': self.details,
            'model': self.model,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cost_usd': str(self.cost_usd) if self.cost_usd is not None else None,
            'latency_ms': self.latency_ms,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'created_at': self.created_at.isoformat(),
            'child_session_id': str(self.child_session_id) if self.child_session_id else None,
        }
```

3. Remove `AgentSessionEvent` / `AgentSessionEventKind` from this file (call sites updated in later tasks — temporarily leave imports broken only within this task’s commit if you stage call-site renames together; prefer completing Task 2–3 in the same PR-ready chunk if imports would otherwise break mid-tree).

4. Update `HourlyUsage` docstring to mention activities.

- [ ] **Step 4: Generate schema migration (do not hand-write)**

```bash
./olib/scripts/orunr django manage makemigrations agent_sessions --name hierarchical_session_activities
```

Expected: new file under `backend/apps/sessions/migrations/` adding `parent_session` and creating `AgentSessionActivity` (and/or renaming/removing `AgentSessionEvent` depending on detection). If Django proposes DeleteModel+CreateModel for the event→activity swap, prefer an explicit rename path: keep the event table through the data migration (Task 3), then DeleteModel in a later generated migration. **Do not invent `CreateModel` field lists by hand.**

If autodetection wants to drop `AgentSessionEvent` immediately, split work:

1. First makemigrations that **only** adds `parent_session` + creates **new** `AgentSessionActivity` while leaving `AgentSessionEvent` in models temporarily (comment retention), OR keep both model classes until after data migration.
2. Practical approach for implementers: **keep both `AgentSessionEvent` and `AgentSessionActivity` in `models.py` until Task 3 data migration lands**, then delete `AgentSessionEvent` and generate `0008_remove_agentsessionevent`.

- [ ] **Step 5: Run migration + tests**

```bash
./olib/scripts/orunr django manage migrate agent_sessions
./olib/scripts/orunr py test backend/apps/sessions/tests/test_activities.py -v
```

Expected: PASS for ancestry + activity basics

- [ ] **Step 6: Commit PR-ready chunk**

```bash
git add backend/apps/sessions/models.py backend/apps/sessions/migrations/ backend/apps/sessions/tests/test_activities.py
git commit -m "$(cat <<'EOF'
feat(sessions): add parent_session and AgentSessionActivity models

EOF
)"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: Persistence API — create/update activities + validation

**Files:**
- Create: `backend/apps/sessions/activities.py`
- Modify: `backend/apps/sessions/services/commands.py`
- Modify: `backend/apps/sessions/services/queries.py`
- Modify: `backend/apps/sessions/tests/test_activities.py`
- Modify: `backend/apps/sessions/tests/test_services.py`

- [ ] **Step 1: Write failing service tests**

```python
# append to test_activities.py
from apps.sessions.services import commands as session_commands
from apps.sessions.services import queries as session_queries
from django.core.exceptions import ValidationError


class TestActivityCommands(OTransactionTestCase):
    def test_create_nested_child_under_same_session(self) -> None:
        session = make_test_session('nest-1')
        parent = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='work',
            summary='doing work',
            details={},
        )
        child = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.STATUS,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='note',
            summary='checkpoint',
            details={},
            parent_id=parent.id,
        )
        self.assertEqual(child.parent_id, parent.id)
        self.assertEqual(child.seq, 2)
        rows = session_queries.activities_for(session)
        self.assertEqual([r.id for r in rows], [parent.id, child.id])

    def test_rejects_cross_session_parent(self) -> None:
        a = make_test_session('nest-a')
        b = make_test_session('nest-b')
        parent = session_commands.create_activity(
            a,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='a',
            summary='',
            details={},
        )
        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                b,
                kind=AgentSessionActivityKind.STATUS,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='bad',
                summary='',
                details={},
                parent_id=parent.id,
            )

    def test_update_increments_revision_and_locks_terminal(self) -> None:
        session = make_test_session('rev-1')
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.RUNNING,
            name='clock__now',
            summary='running',
            details={'call_id': 'c1'},
        )
        updated = session_commands.update_activity(
            row.id,
            status=AgentSessionActivityStatus.SUCCEEDED,
            summary='done',
            details={'call_id': 'c1', 'result': 'ok'},
            latency_ms=12,
        )
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.status, AgentSessionActivityStatus.SUCCEEDED)
        # Terminal immutability: further lifecycle patches raise
        with self.assertRaises(ValidationError):
            session_commands.update_activity(
                row.id,
                status=AgentSessionActivityStatus.FAILED,
                summary='nope',
                details={},
            )
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_activities.py -k ActivityCommands -v`

Expected: FAIL — `create_activity` / `update_activity` missing

- [ ] **Step 3: Implement `activities.py` + commands/queries**

`backend/apps/sessions/activities.py`:

```python
"""Low-level activity persistence (seq + revision). Called only from session services."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityStatus,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

_TERMINAL = frozenset({
    AgentSessionActivityStatus.SUCCEEDED,
    AgentSessionActivityStatus.FAILED,
    AgentSessionActivityStatus.CANCELLED,
})


def _next_seq(session: AgentSession) -> int:
    """Allocate the next immutable creation-order seq for a session."""
    current = AgentSessionActivity.objects.filter(session=session).aggregate(m=Max('seq'))['m']
    return (current or 0) + 1


@transaction.atomic
def create_activity_row(
    session: AgentSession,
    *,
    kind: str,
    status: str,
    name: str,
    summary: str,
    details: dict[str, Any],
    parent_id: UUID | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    started_at=None,
    ended_at=None,
    child_session_id: UUID | None = None,
) -> AgentSessionActivity:
    """Insert one activity; validate parent belongs to the same session."""
    parent = None
    if parent_id is not None:
        try:
            parent = AgentSessionActivity.objects.select_for_update().get(pk=parent_id)
        except AgentSessionActivity.DoesNotExist as exc:
            raise ValidationError({'parent_id': 'parent activity not found'}) from exc
        if parent.session_id != session.id:
            raise ValidationError({'parent_id': 'parent must belong to the same session'})
    now = timezone.now()
    if status == AgentSessionActivityStatus.RUNNING and started_at is None:
        started_at = now
    # Lifecycle kinds get ended_at when created already terminal; message-like kinds may omit it.
    if ended_at is None and status in _TERMINAL and kind in ('tool', 'llm', 'span', 'subagent'):
        ended_at = now
    return AgentSessionActivity.objects.create(
        session=session,
        parent=parent,
        seq=_next_seq(session),
        revision=1,
        kind=kind,
        status=status,
        name=name,
        summary=summary,
        details=details,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        started_at=started_at,
        ended_at=ended_at,
        child_session_id=child_session_id,
    )


@transaction.atomic
def update_activity_row(
    activity_id: UUID,
    *,
    status: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    ended_at=None,
    child_session_id: UUID | None = None,
    allow_terminal_reconcile: bool = False,
) -> AgentSessionActivity:
    """Atomically bump revision; refuse regressing terminal rows unless reconcile."""
    row = AgentSessionActivity.objects.select_for_update().get(pk=activity_id)
    if row.status in _TERMINAL and not allow_terminal_reconcile:
        raise ValidationError({'status': 'terminal activity is immutable'})
    if status is not None:
        row.status = status
    if summary is not None:
        row.summary = summary
    if details is not None:
        row.details = details
    if model is not None:
        row.model = model
    if input_tokens is not None:
        row.input_tokens = input_tokens
    if output_tokens is not None:
        row.output_tokens = output_tokens
    if cost_usd is not None:
        row.cost_usd = cost_usd
    if latency_ms is not None:
        row.latency_ms = latency_ms
    if ended_at is not None:
        row.ended_at = ended_at
    elif status in _TERMINAL and row.ended_at is None:
        row.ended_at = timezone.now()
    if child_session_id is not None:
        row.child_session_id = child_session_id
    row.revision = row.revision + 1
    row.save()
    return row
```

Wire thin wrappers in `commands.py` that call these and (in Task 4) publish. For this task, commands may publish after notify lands — if notify not ready, publish in Task 4; keep create/update pure here and add publish in Task 4 step.

Queries:

```python
def activities_for(session: AgentSession | UUID) -> list[AgentSessionActivity]:
    """Return session activities in immutable creation order."""
    sid = session if isinstance(session, UUID) else session.id
    return list(AgentSessionActivity.objects.filter(session_id=sid).order_by('seq'))


def get_first_input_text(session_id: UUID) -> str | None:
    """First user input activity content for chat-name generation."""
    details = (
        AgentSessionActivity.objects.filter(
            session_id=session_id, kind=AgentSessionActivityKind.INPUT
        )
        .order_by('seq')
        .values_list('details', flat=True)
        .first()
    )
    if not details:
        return None
    content = details.get('content', '')
    if not isinstance(content, str):
        return None
    text = content.strip()
    return text or None


def input_activity_count(session_id: UUID) -> int:
    """Count input activities (replaces input_event_count)."""
    return AgentSessionActivity.objects.filter(
        session_id=session_id, kind=AgentSessionActivityKind.INPUT
    ).count()


def parent_session_breadcrumb(session: AgentSession) -> list[AgentSession]:
    """Walk parent_session links toward the root (nearest parent first)."""
    chain: list[AgentSession] = []
    current = session.parent_session
    seen: set[UUID] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        chain.append(current)
        current = current.parent_session
    return chain
```

Rename all `input_event_count` call sites to `input_activity_count` in the same change.

Update `record_input` to create a terminal `input` activity via `create_activity`:

```python
def record_input(session: AgentSession, content: str) -> AgentSessionActivity:
    """Persist a terminal input activity and publish an upsert (publish wired in Task 4)."""
    row = create_activity(
        session,
        kind=AgentSessionActivityKind.INPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='input',
        summary=(content[:120] + '…') if len(content) > 120 else content,
        details={'content': content},
    )
    # Task 4: publish_session_activity(session.id, row.to_stream_dict())
    if input_activity_count(session.id) == 1 and DEFAULT_CHAT_NAME_CONFIG.enabled:
        transaction.on_commit(lambda: _schedule_generate_session_name(session.id))
    return row
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_activities.py -k ActivityCommands -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/sessions/activities.py backend/apps/sessions/services/ backend/apps/sessions/tests/
git commit -m "$(cat <<'EOF'
feat(sessions): add activity create/update services with revision locking

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 3: Historical data migration (events → activities)

**Files:**
- Generate empty: `backend/apps/sessions/migrations/0007_migrate_events_to_activities.py`
- Create: `backend/apps/sessions/tests/test_activity_migration.py`
- Later generate: drop `AgentSessionEvent` if still present

- [ ] **Step 1: Write migration unit tests against the forwards function**

Follow the keys pattern (`import_module` + call `forwards`):

```python
# backend/apps/sessions/tests/test_activity_migration.py
from decimal import Decimal
from importlib import import_module
from uuid import uuid4

from apps.sessions.tests.base import make_test_session
from django.apps import apps as django_apps
from django.db import connection

from olib.py.django.test.cases import OTestCase


class TestMigrateEventsToActivities(OTestCase):
    """Call the data-migration forwards while AgentSessionEvent still exists."""

    def setUp(self) -> None:
        super().setUp()
        mod = import_module('apps.sessions.migrations.0007_migrate_events_to_activities')
        self.forwards = mod.forwards
        self.Event = django_apps.get_model('agent_sessions', 'AgentSessionEvent')
        self.Activity = django_apps.get_model('agent_sessions', 'AgentSessionActivity')

    def test_pairs_tool_call_and_result(self) -> None:
        session = make_test_session('mig-pair')
        call_id = str(uuid4())
        self.Event.objects.create(
            id=uuid4(),
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={
                'call_id': call_id,
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
            },
        )
        self.Event.objects.create(
            id=uuid4(),
            session_id=session.id,
            seq=2,
            kind='TOOL_RESULT',
            payload={'call_id': call_id, 'content': '2026-01-01T00:00:00+00:00'},
            latency_ms=5,
        )
        self.forwards(django_apps, connection.schema_editor())
        tools = list(self.Activity.objects.filter(session_id=session.id, kind='tool'))
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].status, 'succeeded')
        self.assertEqual(tools[0].details['result'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(tools[0].latency_ms, 5)
        self.assertEqual(self.Activity.objects.filter(session_id=session.id).count(), 1)

    def test_orphan_tool_call_becomes_failed(self) -> None:
        session = make_test_session('mig-orphan-call')
        self.Event.objects.create(
            id=uuid4(),
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={'call_id': 'x', 'instance_id': 'clock', 'function': 'now', 'arguments': {}},
        )
        self.forwards(django_apps, connection.schema_editor())
        tool = self.Activity.objects.get(session_id=session.id, kind='tool')
        self.assertEqual(tool.status, 'failed')
        self.assertTrue(tool.details.get('legacy_orphan'))

    def test_orphan_tool_result_becomes_status(self) -> None:
        session = make_test_session('mig-orphan-result')
        self.Event.objects.create(
            id=uuid4(),
            session_id=session.id,
            seq=1,
            kind='TOOL_RESULT',
            payload={'call_id': 'missing', 'content': 'late'},
        )
        self.forwards(django_apps, connection.schema_editor())
        row = self.Activity.objects.get(session_id=session.id)
        self.assertEqual(row.kind, 'status')
        self.assertTrue(row.details.get('legacy_orphan_tool_result'))

    def test_preserves_input_output_usage(self) -> None:
        session = make_test_session('mig-usage')
        self.Event.objects.create(
            id=uuid4(), session_id=session.id, seq=1, kind='INPUT', payload={'content': 'hi'}
        )
        self.Event.objects.create(
            id=uuid4(),
            session_id=session.id,
            seq=2,
            kind='OUTPUT',
            payload={'content': 'yo'},
            model='gpt-5.4-mini',
            input_tokens=10,
            output_tokens=2,
            cost_usd=Decimal('0.001000'),
        )
        self.forwards(django_apps, connection.schema_editor())
        out = self.Activity.objects.get(session_id=session.id, kind='output')
        self.assertEqual(out.input_tokens, 10)
        self.assertEqual(str(out.cost_usd), '0.001000')
```

- [ ] **Step 2: Generate empty migration**

```bash
./olib/scripts/orunr django manage makemigrations agent_sessions --empty --name migrate_events_to_activities
```

Expected: empty migration file created (do not invent schema ops)

- [ ] **Step 3: Implement `forwards` / `backwards`**

Mapping rules (exact):

| Legacy `kind` | Result |
|---------------|--------|
| `INPUT` | `kind=input`, `status=succeeded`, `name=input`, `details={content}` |
| `OUTPUT` | `kind=output`, `status=succeeded`, `name=output`, copy usage fields onto activity; `details={content}` |
| `FAILURE` | `kind=failure`, `status=failed`, `name=failure`, `details=payload` |
| `RESTART` | `kind=restart`, `status=succeeded`, `name=restart`, `details={}` |
| `TOOL_CALL` + matching `TOOL_RESULT` by `payload.call_id` | Keep call id/seq as `tool`; merge result `content` + `latency_ms` into `details`; status `failed` if result JSON has `failure` key or `ok is False`, else `succeeded`; **delete** result row (do not copy it) |
| Orphan `TOOL_CALL` | `tool` + `status=failed` + `details.legacy_orphan=True` |
| Orphan `TOOL_RESULT` | `status` activity with advisory details; preserve seq |

Preserve event `id` on the surviving activity row when inserting into the new table (same UUID). Seq gaps after deleting paired results are intentional.

```python
import json
from decimal import Decimal


def _result_is_failure(content: object) -> bool:
    """Treat structured tool failure payloads as failed tool activities."""
    if not isinstance(content, str):
        return False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    if 'failure' in parsed:
        return True
    return parsed.get('ok') is False


def forwards(apps, schema_editor):
    """Copy/transform AgentSessionEvent rows into AgentSessionActivity; drop paired results."""
    Event = apps.get_model('agent_sessions', 'AgentSessionEvent')
    Activity = apps.get_model('agent_sessions', 'AgentSessionActivity')
    # Per session: index TOOL_RESULT by call_id, then emit activities.
    session_ids = Event.objects.values_list('session_id', flat=True).distinct()
    for session_id in session_ids:
        events = list(Event.objects.filter(session_id=session_id).order_by('seq'))
        results_by_call: dict[str, object] = {}
        for ev in events:
            if ev.kind == 'TOOL_RESULT':
                results_by_call[ev.payload.get('call_id')] = ev
        consumed_result_ids: set = set()
        for ev in events:
            if ev.kind == 'TOOL_RESULT':
                if ev.id in consumed_result_ids:
                    continue
                # Orphan result → status advisory activity
                Activity.objects.create(
                    id=ev.id,
                    session_id=session_id,
                    seq=ev.seq,
                    revision=1,
                    kind='status',
                    status='succeeded',
                    name='legacy_tool_result',
                    summary='Orphan legacy tool result',
                    details={
                        'legacy_orphan_tool_result': True,
                        'call_id': ev.payload.get('call_id'),
                        'content': ev.payload.get('content'),
                    },
                    latency_ms=ev.latency_ms,
                )
                Activity.objects.filter(pk=ev.id).update(created_at=ev.created_at)
                continue
            if ev.kind == 'TOOL_CALL':
                call_id = ev.payload.get('call_id')
                result = results_by_call.get(call_id)
                details = dict(ev.payload)
                status = 'failed'
                latency_ms = ev.latency_ms
                if result is not None:
                    consumed_result_ids.add(result.id)
                    details['result'] = result.payload.get('content')
                    latency_ms = result.latency_ms or latency_ms
                    status = 'failed' if _result_is_failure(details['result']) else 'succeeded'
                else:
                    details['legacy_orphan'] = True
                Activity.objects.create(
                    id=ev.id,
                    session_id=session_id,
                    seq=ev.seq,
                    revision=1,
                    kind='tool',
                    status=status,
                    name=f"{details.get('instance_id') or details.get('tool')}__{details.get('function')}",
                    summary=details.get('function') or 'tool',
                    details=details,
                    latency_ms=latency_ms,
                )
                Activity.objects.filter(pk=ev.id).update(created_at=ev.created_at)
                continue
            kind_map = {
                'INPUT': ('input', 'succeeded', 'input'),
                'OUTPUT': ('output', 'succeeded', 'output'),
                'FAILURE': ('failure', 'failed', 'failure'),
                'RESTART': ('restart', 'succeeded', 'restart'),
            }
            kind, status, name = kind_map[ev.kind]
            Activity.objects.create(
                id=ev.id,
                session_id=session_id,
                seq=ev.seq,
                revision=1,
                kind=kind,
                status=status,
                name=name,
                summary='',
                details=ev.payload or {},
                model=ev.model,
                input_tokens=ev.input_tokens,
                output_tokens=ev.output_tokens,
                cost_usd=ev.cost_usd,
                latency_ms=ev.latency_ms,
            )
            Activity.objects.filter(pk=ev.id).update(created_at=ev.created_at)
        Event.objects.filter(session_id=session_id).delete()


def backwards(apps, schema_editor):
    """Irreversible: paired TOOL_RESULT rows cannot be reconstructed losslessly."""
    raise RuntimeError('migrate_events_to_activities is irreversible')
```

- [ ] **Step 4: Run migration tests**

```bash
./olib/scripts/orunr django manage migrate agent_sessions
./olib/scripts/orunr py test backend/apps/sessions/tests/test_activity_migration.py -v
```

Expected: PASS

- [ ] **Step 5: Remove `AgentSessionEvent` model + generate drop migration**

Delete class from `models.py`, update admin, then:

```bash
./olib/scripts/orunr django manage makemigrations agent_sessions --name remove_agentsessionevent
```

Delete `backend/apps/sessions/events.py` only after all imports moved (Tasks 2–5). No re-export shim.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/sessions/migrations/ backend/apps/sessions/tests/test_activity_migration.py backend/apps/sessions/models.py backend/apps/sessions/admin.py
git commit -m "$(cat <<'EOF'
feat(sessions): migrate flat events into hierarchical activities

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Notify + SSE upsert protocol + minimal session page adapter

**Files:**
- Modify: `backend/apps/sessions/notify.py`
- Modify: `backend/apps/sessions/services/commands.py` (publish on create/update)
- Modify: `backend/apps/web/views.py` (`session_events_sse`)
- Modify: `backend/templates/web/session_detail.html`
- Modify: `backend/apps/web/tests/test_sse.py`
- Modify: `backend/apps/web/tests/test_session_dialog.py` (listener name if changed)

- [ ] **Step 1: Failing SSE test**

```python
# test_sse.py — replay activities as upsert envelopes
from apps.sessions.services import commands as session_commands
from apps.sessions.models import AgentSessionActivityKind, AgentSessionActivityStatus

def test_replays_activity_upserts_from_db(self) -> None:
    session = make_test_session('sse-act')
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.INPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='input',
        summary='ping',
        details={'content': 'ping'},
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='output',
        summary='pong',
        details={'content': 'pong'},
    )

    async def collect() -> str:
        client = AsyncClient()
        user = await sync_to_async(get_user_model().objects.get)(username='user-sse-act')
        await sync_to_async(client.force_login)(user)
        response = await client.get(f'/sessions/{session.id}/events/')
        assert isinstance(response, StreamingHttpResponse)
        parts: list[bytes] = []
        async for part in cast(AsyncIterator[bytes], response.streaming_content):
            parts.append(part)
        return b''.join(parts).decode()

    body = asyncio.run(collect())
    self.assertIn('event: session_activity', body)
    self.assertIn('"operation": "upsert"', body)
    self.assertIn('"kind": "input"', body)
```

Add `test_upsert_keeps_highest_revision_on_replay`: create a running tool, update to succeeded (revision 2), reconnect SSE, assert body contains `"revision": 2` once for that activity id and does not emit a stale revision-1 after it.

- [ ] **Step 2: Run — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/web/tests/test_sse.py -v`

Expected: FAIL — still `session_event` / missing upsert shape

- [ ] **Step 3: Implement notify + SSE + template**

`notify.py`:

```python
SessionChannel = Literal['session_activity', 'session_update']

def publish_session_activity(session_id: UUID | str, activity_dict: dict[str, Any]) -> None:
    """Publish an idempotent activity upsert for live clients."""
    payload = {'operation': 'upsert', 'activity': activity_dict}
    publish_session_message(session_id, session_message('session_activity', payload))
```

Delete `publish_session_event` (update all call sites — no shim).

`session_events_sse` in `views.py`:

- Replay `session_queries.activities_for(session_id)` as `session_activity` upserts
- Track `last_seen: dict[activity_id, revision]`
- On pubsub `session_activity`: apply if `revision > last_seen.get(id, 0)`
- Keep `session_update` handling

`session_detail.html` Alpine (minimal, flat — **not** nested UI):

- Listen to `session_activity`
- Upsert into `events` map/list by `activity.id`, keep max revision
- Sort display by `seq`
- `formatPayload`: `input`/`output` → `details.content`; `failure` → `details.message`; else JSON `details`
- Rich content: treat `kind === 'output'` (lowercase)
- Cost sum: still sum `cost_usd` on rows (LLM rows hold usage; outputs should not double-count after loop change)

- [ ] **Step 4: Run SSE + dialog tests**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_sse.py backend/apps/web/tests/test_session_dialog.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(sessions): stream activity upserts over SSE

EOF
)"
```

---

### Task 5: Rebuild + hourly aggregation

**Files:**
- Modify: `backend/apps/sessions/rebuild.py`
- Modify: `backend/apps/sessions/tasks.py`
- Modify: `backend/apps/sessions/tests/test_rebuild.py`
- Modify: `backend/apps/sessions/tests/test_aggregation.py`

- [ ] **Step 1: Failing rebuild tests**

```python
def test_unified_tool_expands_to_assistant_and_tool_messages(self) -> None:
    session = make_test_session()
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.INPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='input',
        summary='What time is it?',
        details={'content': 'What time is it?'},
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='output',
        summary='Let me check.',
        details={'content': 'Let me check.'},
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.TOOL,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='clock__now',
        summary='clock.now',
        details={
            'call_id': 'c1',
            'instance_id': 'clock',
            'function': 'now',
            'arguments': {},
            'result': '2026-01-01T00:00:00+00:00',
        },
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='output',
        summary='It is midnight UTC.',
        details={'content': 'It is midnight UTC.'},
    )
    messages = rebuild_messages(session, system_prompt=load_example('clock-assistant').system_prompt)
    self.assertEqual(messages[1], {'role': 'user', 'content': 'What time is it?'})
    self.assertIn('tool_calls', messages[2])
    self.assertEqual(messages[3]['role'], 'tool')
    self.assertEqual(messages[3]['tool_call_id'], 'c1')


def test_llm_span_status_omitted_from_provider_messages(self) -> None:
    session = make_test_session('rebuild-skip')
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.INPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='input',
        summary='go',
        details={'content': 'go'},
    )
    llm = session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.LLM,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='gpt',
        summary='generate',
        details={},
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='inner',
        summary='',
        details={},
        parent_id=llm.id,
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.STATUS,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='note',
        summary='ok',
        details={},
        parent_id=llm.id,
    )
    session_commands.create_activity(
        session,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='output',
        summary='done',
        details={'content': 'done'},
        parent_id=llm.id,
    )
    roles = [m['role'] for m in rebuild_messages(session, system_prompt='sys')]
    self.assertEqual(roles, ['system', 'user', 'assistant'])
```

Aggregation: seed terminal `llm` (with tokens/cost) and `tool` activities; `aggregate_hourly_usage` counts iterations from `llm` and tool_call_count from `tool`; do **not** count `output` children for iterations/cost.

- [ ] **Step 2: Run — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/sessions/tests/test_rebuild.py backend/apps/sessions/tests/test_aggregation.py -v`

- [ ] **Step 3: Implement rebuild + aggregation**

`rebuild_messages_from_activities`:

- Order by `seq`
- `input` → user; `output` → assistant content
- `tool` → synthesize assistant `tool_calls` entry + following `role=tool` message from `details.result` (string or JSON-serialized)
- Skip `llm`, `span`, `status`, `subagent`, `failure`, `restart`

`aggregate_hourly_usage`: filter `kind=llm` (terminal preferred / all with cost) and `kind=tool` instead of OUTPUT/TOOL_CALL.

Rename helpers: `rebuild_messages_from_events` → `rebuild_messages_from_activities`; update imports; delete old name (no alias).

- [ ] **Step 4: PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(sessions): rebuild provider messages and usage from activities

EOF
)"
```

---

### Task 6: Runner backends + `ActivityRecorder` + `ToolContext`

**Files:**
- Create: `backend/libs/tools/activity.py`
- Modify: `backend/libs/tools/context.py`, `backend/libs/tools/__init__.py`
- Modify: `backend/apps/runner/backends/base.py`, `django.py`, `memory.py`
- Create: `backend/apps/runner/activity_recorder.py`
- Create: `backend/apps/runner/tests/test_activity_recorder.py`

- [ ] **Step 1: Failing protocol/recorder tests**

```python
# libs-level: NoOp recorder does nothing
# memory backend: start tool → nested span → complete tool; children.parent_id set
# django backend: same via services (OTransactionTestCase)
```

Define:

```python
# libs/tools/activity.py
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID
from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityRef:
    """Opaque handle returned by recorder start/complete/fail."""

    id: UUID
    seq: int
    revision: int
    kind: str
    status: str


class ActivityRecorder(Protocol):
    """Django-free recording API injected into ToolContext."""

    def start(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'running',
    ) -> ActivityRef:
        """Create a lifecycle activity under the current parent scope."""

    def complete(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'succeeded',
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Any = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Mark an activity terminal success (or explicit status)."""

    def fail(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Mark an activity terminal failed."""

    def status_note(
        self,
        *,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Emit a terminal status activity under the current parent."""

    def span(self, *, name: str, summary: str = '') -> AbstractContextManager[ActivityRef]:
        """Start a span, push parent scope, complete on exit (fail on raised exception)."""

    def push_parent(self, activity_id: UUID | None) -> AbstractContextManager[None]:
        """Temporarily set the parent activity for nested creates."""

    def link_subagent(
        self,
        *,
        agent_id: UUID,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Create a subagent activity and linked child session (Django backend only)."""


class NoOpActivityRecorder:
    """Recorder used when a tool runs outside a session (tests / offline bind)."""

    def start(self, **kwargs: Any) -> ActivityRef:
        """Return a synthetic ref without persistence."""
        from uuid import uuid4

        return ActivityRef(
            id=uuid4(),
            seq=0,
            revision=1,
            kind=str(kwargs.get('kind', '')),
            status=str(kwargs.get('status', '')),
        )

    def complete(self, activity_id: UUID, **kwargs: Any) -> ActivityRef:
        """No-op complete."""
        return ActivityRef(id=activity_id, seq=0, revision=1, kind='', status=str(kwargs.get('status', 'succeeded')))

    def fail(self, activity_id: UUID, **kwargs: Any) -> ActivityRef:
        """No-op fail."""
        return ActivityRef(id=activity_id, seq=0, revision=1, kind='', status='failed')

    def status_note(self, **kwargs: Any) -> ActivityRef:
        """No-op status note."""
        return self.start(kind='status', status='succeeded', name=kwargs.get('name', ''), summary=kwargs.get('summary', ''))

    def span(self, *, name: str, summary: str = '') -> AbstractContextManager[ActivityRef]:
        """No-op span context manager."""
        from contextlib import nullcontext

        ref = self.start(kind='span', name=name, summary=summary, status='running')
        return nullcontext(ref)

    def push_parent(self, activity_id: UUID | None) -> AbstractContextManager[None]:
        """No-op parent push."""
        from contextlib import nullcontext

        return nullcontext()

    def link_subagent(self, **kwargs: Any) -> ActivityRef:
        """Refuse sub-agent linking outside a session."""
        raise RuntimeError('sub-agent linking requires a session recorder')
```

`ToolContext` gains `recorder: ActivityRecorder = field(default_factory=NoOpActivityRecorder)`.

`RecordedActivity` replaces `RecordedEvent` in `backends/base.py` with fields mirroring the stream dict (including `parent_id`, `revision`, `status`, `name`, `summary`, `details`, `child_session_id`). Replace `append_event` with abstract methods:

```python
def create_activity(
    self,
    *,
    kind: str,
    status: str,
    name: str,
    summary: str,
    details: dict[str, Any],
    parent_id: uuid.UUID | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    child_session_id: uuid.UUID | None = None,
) -> RecordedActivity:
    """Persist a new activity under this session."""

def update_activity(
    self,
    activity_id: uuid.UUID,
    *,
    status: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
    allow_terminal_reconcile: bool = False,
) -> RecordedActivity:
    """Update an existing activity and bump revision."""

def activities(self) -> list[RecordedActivity]:
    """Return activities in seq order."""

def publish_activity(self, activity: RecordedActivity) -> None:
    """Publish an upsert envelope for the activity."""

def record_input(self, content: str) -> RecordedActivity:
    """Create a terminal input activity (and side effects via services on Django)."""
```

`BackendActivityRecorder` in `apps/runner/activity_recorder.py` maintains a parent stack; `start` uses current parent; `span` pushes/pops.

- [ ] **Step 2–4: TDD implement until memory+django parity tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(runner): add activity recorder protocol and session backends

EOF
)"
```

---

### Task 7: Loop — LLM + tool lifecycle (nested)

**Files:**
- Modify: `backend/apps/runner/loop.py`
- Modify: `backend/apps/runner/tests/test_loop.py`
- Modify: `backend/apps/runner/tests/usecases/test_inbox_functional.py`

- [ ] **Step 1: Failing loop tests**

```python
def test_tool_is_single_running_then_terminal_activity(self) -> None:
    backend = self._backend()
    backend.push_mailbox({'action': 'chat', 'content': 'time?'})
    tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': '1'}])
    follow_up = StreamResult(content='done')
    with patch(
        'apps.runner.loop.make_provider',
        return_value=FakeProvider.for_responses([tool_call, follow_up]),
    ):
        SessionRunner(backend).run()
    tools = [a for a in backend.activities() if a.kind == 'tool']
    self.assertEqual(len(tools), 1)
    self.assertEqual(tools[0].status, 'succeeded')
    self.assertIn('result', tools[0].details)
    self.assertFalse(any(a.kind == 'TOOL_RESULT' for a in backend.activities()))


def test_output_and_tools_nest_under_llm_activity(self) -> None:
    backend = self._backend()
    backend.push_mailbox({'action': 'chat', 'content': 'time?'})
    tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': '1'}])
    follow_up = StreamResult(content='done')
    with patch(
        'apps.runner.loop.make_provider',
        return_value=FakeProvider.for_responses([tool_call, follow_up]),
    ):
        SessionRunner(backend).run()
    llms = [a for a in backend.activities() if a.kind == 'llm']
    self.assertEqual(len(llms), 2)  # tool turn + follow-up turn
    first_llm = llms[0]
    tools = [a for a in backend.activities() if a.kind == 'tool']
    self.assertEqual(tools[0].parent_id, first_llm.id)
    for out in [a for a in backend.activities() if a.kind == 'output']:
        self.assertIsNone(out.cost_usd)
        self.assertIsNotNone(out.parent_id)


def test_failed_tool_marks_terminal_failed(self) -> None:
    backend = self._backend()
    backend.push_mailbox({'action': 'chat', 'content': 'nope'})
    denied = StreamResult(
        content='',
        tool_calls=[{'name': 'clock__unknown_fn', 'arguments': {}, 'id': 'deny-1'}],
    )
    follow_up = StreamResult(content='ok')
    with patch(
        'apps.runner.loop.make_provider',
        return_value=FakeProvider.for_responses([denied, follow_up]),
    ):
        SessionRunner(backend).run()
    tool = next(a for a in backend.activities() if a.kind == 'tool')
    self.assertEqual(tool.status, 'failed')


def test_provider_failure_completes_llm_then_records_failure(self) -> None:
    backend = self._backend()
    backend.push_mailbox({'action': 'chat', 'content': 'ping'})
    error_result = StreamResult(error=ProviderError(message='Provider unavailable', code='provider_failure'))
    with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([error_result])):
        SessionRunner(backend).run()
    llms = [a for a in backend.activities() if a.kind == 'llm']
    self.assertTrue(llms)
    self.assertEqual(llms[0].status, 'failed')
    self.assertTrue(any(a.kind == 'failure' for a in backend.activities()))
```

Update inbox functional assertions: look for `kind == 'tool'` and `details['instance_id']`; drop `TOOL_RESULT` expectation; observability log may say `session_activity` instead of `session_event`.

- [ ] **Step 2: Run — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_loop.py -v`

- [ ] **Step 3: Rewrite loop recording**

In `SessionRunner.__init__`, build `BackendActivityRecorder(self.backend)` and pass into `ToolContext(..., recorder=self.recorder)`.

`_emit_output` becomes:

1. `llm_ref = recorder.start(kind='llm', name=usage.model or self.config_spec.llm.model, summary='generate', details={})`
2. `with recorder.push_parent(llm_ref.id):` create `output` child via `backend.create_activity(kind='output', status='succeeded', details={'content': result.content}, ...)` with **no** usage fields
3. For each tool call, `_handle_tool_call` while still under llm parent (or re-push)
4. `recorder.complete(llm_ref.id, summary='generate', details={}, model=..., input_tokens=..., output_tokens=..., cost_usd=..., latency_ms=..., status='succeeded')` — or `recorder.fail` when provider returns an error before output

`_handle_tool_call`:

1. `start` tool `running` with call_id/args **before** invoke
2. `with recorder.push_parent(tool_id):` invoke bound tool (nested spans possible)
3. `complete` or `fail` same activity with result + latency
4. Keep `on_tool_call_start` / `on_tool_call_end` hooks for eval parity

`_record_failure` / restart: create terminal `failure` / `restart` activities via backend (no parent unless inside a scope).

Provider errors: fail/complete open LLM activity first, then session `failure` activity.

- [ ] **Step 4: PASS loop + inbox functional**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_loop.py backend/apps/runner/tests/usecases/test_inbox_functional.py -v
```

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(runner): record nested LLM and unified tool activities

EOF
)"
```

---

### Task 8: Hooks + observability adapters

**Files:**
- Modify: `backend/apps/runner/hooks.py`
- Modify: `backend/apps/runner/usecases/observability.py`
- Modify: `backend/apps/runner/tests/test_hooks.py`

- [ ] **Step 1: Failing hook tests**

Expect `on_activity_created` / `on_activity_updated` firings for input/llm/output/tool; retain generate/tool_call hooks; remove dependence on `on_event` **or** keep `on_event` as a thin adapter that mirrors created activities for one release — design says move to created/updated; **delete `on_event`** and update observability (no shim field).

```python
@dataclass(frozen=True)
class HookSet:
    on_run_start: Callable[[], None] | None = None
    on_run_end: Callable[[], None] | None = None
    on_generate_start: Callable[[list[dict[str, Any]], list[Any]], None] | None = None
    on_generate_end: Callable[[Any], None] | None = None
    on_tool_call_start: Callable[[dict[str, Any]], None] | None = None
    on_tool_call_end: Callable[[dict[str, Any], str], None] | None = None
    on_activity_created: Callable[[Any], None] | None = None
    on_activity_updated: Callable[[Any], None] | None = None
    on_status: Callable[[str], None] | None = None
```

Observability JSONL (`usecases/observability.py`): replace `on_event` with:

```python
def on_activity_created(activity: RecordedActivity) -> None:
    """Mirror a newly persisted activity into the eval event log."""
    emit(f'[activity] create {activity.seq} {activity.kind} {activity.status}')
    append({'event': 'session_activity', 'op': 'create', 'record': _activity_record(activity)})


def on_activity_updated(activity: RecordedActivity) -> None:
    """Mirror an activity revision into the eval event log."""
    emit(f'[activity] update {activity.seq} {activity.kind} rev={activity.revision}')
    append({'event': 'session_activity', 'op': 'update', 'record': _activity_record(activity)})
```

Fire `on_activity_created` from backend/recorder after create; fire `on_activity_updated` after update (from `SessionRunner` wrappers that call the recorder). Update `test_hooks.py` expectations accordingly.

- [ ] **Step 2–4: Implement until hooks tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(runner): switch observability hooks to activity create/update

EOF
)"
```

---

### Task 9: Sub-agent session linkage + status reconciliation

**Files:**
- Modify: `backend/apps/sessions/services/commands.py`
- Modify: `backend/apps/runner/session_start.py` and/or `start.py`
- Create: `backend/apps/runner/tests/test_child_session.py`
- Modify: `backend/apps/runner/backends/django.py` (`set_status` reconcile)
- Modify: `backend/apps/sessions/tests/test_activities.py` (cycle / same-user)

- [ ] **Step 1: Failing tests**

```python
def test_start_linked_child_session_atomic(self) -> None:
    parent = make_test_session('sub-parent')
    parent_activity = session_commands.create_activity(
        parent,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.RUNNING,
        name='delegate',
        summary='starting child',
        details={},
    )
    trigger = parent.agent.triggers.filter(name='manual').first()
    child = session_commands.start_linked_child_session(
        parent_session=parent,
        parent_activity_id=parent_activity.id,
        agent=parent.agent,
        trigger=trigger,
    )
    self.assertEqual(child.parent_session_id, parent.id)
    ref = AgentSessionActivity.objects.get(kind='subagent', child_session=child)
    self.assertEqual(ref.session_id, parent.id)
    self.assertEqual(ref.parent_id, parent_activity.id)


def test_rejects_self_parent_and_cycle(self) -> None:
    session = make_test_session('cycle')
    with self.assertRaises(ValidationError):
        session.parent_session = session
        session.save()
    # Also reject start_linked_child_session when parent_session_id walks into a cycle
    a = make_test_session('cyc-a')
    b = AgentSession.objects.create(
        agent=a.agent,
        agent_config=a.agent_config,
        status=a.status,
        trigger_type=a.trigger_type,
        parent_session=a,
    )
    with self.assertRaises(ValidationError):
        session_commands._assert_no_ancestry_cycle(child_parent=b, new_parent=b)


def test_rejects_cross_user_parent(self) -> None:
    parent = make_test_session('user-a-parent')
    other = make_test_session('user-b-child-agent')
    span = session_commands.create_activity(
        parent,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.RUNNING,
        name='x',
        summary='',
        details={},
    )
    with self.assertRaises(ValidationError):
        session_commands.start_linked_child_session(
            parent_session=parent,
            parent_activity_id=span.id,
            agent=other.agent,
            trigger=other.agent.triggers.filter(name='manual').first(),
        )


def test_child_status_reconciles_parent_subagent_activity(self) -> None:
    parent = make_test_session('recon-parent')
    span = session_commands.create_activity(
        parent,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.RUNNING,
        name='x',
        summary='',
        details={},
    )
    child = session_commands.start_linked_child_session(
        parent_session=parent,
        parent_activity_id=span.id,
        agent=parent.agent,
        trigger=parent.agent.triggers.filter(name='manual').first(),
        dispatch=False,
    )
    child.status = AgentSessionStatus.DONE
    child.save(update_fields=['status'])
    session_commands.reconcile_subagent_activity(child)
    ref = AgentSessionActivity.objects.get(child_session=child)
    self.assertEqual(ref.status, AgentSessionActivityStatus.SUCCEEDED)
    self.assertGreaterEqual(ref.revision, 2)


def test_startup_failure_rolls_back_half_link(self) -> None:
    parent = make_test_session('rollback-parent')
    span = session_commands.create_activity(
        parent,
        kind=AgentSessionActivityKind.SPAN,
        status=AgentSessionActivityStatus.RUNNING,
        name='x',
        summary='',
        details={},
    )
    agent = parent.agent
    agent.current_config = None
    agent.save(update_fields=['current_config'])
    with self.assertRaises(Exception):
        session_commands.start_linked_child_session(
            parent_session=parent,
            parent_activity_id=span.id,
            agent=agent,
            trigger=None,
        )
    self.assertFalse(AgentSessionActivity.objects.filter(session=parent, kind='subagent').exists())
    self.assertFalse(AgentSession.objects.filter(parent_session=parent).exists())
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `start_linked_child_session`**

Atomic `@transaction.atomic`:

1. Authorize `parent_session.agent.user_id == child_agent.user_id`
2. Validate `parent_activity` belongs to `parent_session`
3. Reject if `parent_session_id` would create a cycle (walk parents)
4. Create `subagent` activity (`running`, name=child agent identifier, details={})
5. Create child `AgentSession(parent_session=parent, trigger_type=TriggerType.TOOL_CALL, …)`
6. Set `child_session` OneToOne on the activity
7. `transaction.on_commit` → `maybe_dispatch_session(child.id)` (or accept optional `dispatch=True`)

Expose recorder method `link_subagent(...)` that calls this command through the Django backend only (memory backend can simulate in-memory child id for unit tests).

On `DjangoSessionBackend.set_status`, if session has `parent_activity`, call `commands.reconcile_subagent_activity(child_session)` with `allow_terminal_reconcile=True` for summary/status patches only.

- [ ] **Step 4: PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(sessions): link sub-agent sessions through parent activities

EOF
)"
```

---

### Task 10: Call-site sweep + full verification

**Files:** any remaining `AgentSessionEvent`, `append_event`, `events_for`, `publish_session_event`, `RecordedEvent`, `on_event`, `TOOL_CALL` session-log references under `backend/apps/` (grep). Also `admin.py`, `test_tasks.py`, `test_services.py`, `test_limits.py` if they construct events.

- [ ] **Step 1: Grep for leftover symbols**

```bash
rg -n 'AgentSessionEvent|append_event|events_for|publish_session_event|RecordedEvent|on_event|AgentSessionEventKind' backend/apps backend/libs/tools --glob '*.py'
```

Expected after fixes: no matches except historical migration modules and test_activity_migration seeding comments.

- [ ] **Step 2: Fix stragglers with tests green**

- [ ] **Step 3: Full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
chore(sessions): finish activity cutover and remove legacy event APIs

EOF
)"
```

---

## S_final — Code review (mandatory)

### Task 11: Code review

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

- `{DESCRIPTION}` — hierarchical `AgentSessionActivity` tree, unified tools, nested LLM/span, sub-agent session linkage, SSE upserts, historical migration
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-26-hierarchical-session-activities/2026-07-26-hierarchical-session-activities-design.md` and `-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Write `docs/specs/2026-07-26-hierarchical-session-activities/2026-07-26-hierarchical-session-activities-review.md` per `review-file-template.md`.

- [ ] **Step 5: Track feedback**

Update **Status** to **Fixed** / **Rejected** as the user decides.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` (PR / merge options). Do **not** check epic/spec boxes unless the user explicitly approves after review.

---

## Out of scope

- Nested activity UI / recursive tree visualization (spec 3)
- Concrete user-facing sub-agent tool implementation
- Integration-result normalization (spec 1)
- Changing provider wire formats beyond rebuild expansion of unified tools

## References

- Design: [`2026-07-26-hierarchical-session-activities-design.md`](./2026-07-26-hierarchical-session-activities-design.md)
- Epic: [`../../epics/2026-07-26-agent-context-activity-clarity.md`](../../epics/2026-07-26-agent-context-activity-clarity.md)
- Architecture: [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)
- Current anchors: `backend/apps/sessions/{models,events,rebuild,notify,tasks,services/*}.py`, `backend/apps/runner/{loop,hooks,backends/*,session_start,start,dispatch}.py`, `backend/libs/tools/context.py`, `backend/apps/web/views.py` (`session_events_sse`), `backend/templates/web/session_detail.html`
