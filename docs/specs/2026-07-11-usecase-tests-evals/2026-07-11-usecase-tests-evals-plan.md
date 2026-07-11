# Usecase tests and evals Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **11 of 11** · Item: **Usecase tests and evals**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Ship SessionRunner hooks, reusable olib eval CLI/library with event-log writer, Gmail/ClickUp Protocols + in-memory mocks, thin usecase setup helpers, inbox functional tests (FakeProvider), and a first inbox eval suite with model matrix.

**Architecture:** `SessionRunner` remains the only agent loop (`MemorySessionBackend` by default). Callers register generic hooks for observability. olib owns `EventLogWriter` + `orunr eval` (matrix/scoring protocols). Chief owns mocks, setup helpers, functional scenarios, and the eval suite plugin. No parallel UsecaseHarness runner; no mock flags in agent YAML.

**Tech Stack:** Django 5.2, Celery eager (existing test default), `FakeProvider`, click CLI via olib `orunr`, pydantic for scenario/score shapes.

**Branch:** `feat/2026-07-11-usecase-tests-evals`

**Design spec:** [`2026-07-11-usecase-tests-evals-design.md`](./2026-07-11-usecase-tests-evals-design.md)
**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

> **olib submodule:** Stages that touch `olib/**` are committed **inside the olib submodule** (same branch name if possible), then chief updates the submodule pointer. Never leave olib changes uncommitted only in the parent repo.

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-11-usecase-tests-evals || git checkout -b feat/2026-07-11-usecase-tests-evals
git branch --show-current   # must print feat/2026-07-11-usecase-tests-evals
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Ensure review template exists**

Create `docs/specs/2026-07-11-usecase-tests-evals/2026-07-11-usecase-tests-evals-revision.md`:

```markdown
# Usecase tests and evals — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [ ]   <!-- Implementer: mark [x] only when this item is done. Do not edit other sections. -->
```

- [ ] **Step 0c: Commit plan + revision stub (if uncommitted)**

```bash
git add docs/specs/2026-07-11-usecase-tests-evals/
git commit -m "docs: add usecase tests and evals implementation plan"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

Skip 0c if already committed on the feature branch. Stop on rebase conflicts.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped `./olib/scripts/orunr py test <path>` while iterating — see `ai/commands/py-checks.md`)
- olib-only stages: from repo root still use `./olib/scripts/orunr …`; commit inside `olib/` with its own git
- Test base: `OTestCase` from `olib.py.django.test.cases` for Django/chief tests; plain unittest for olib `py/eval` tests
- **Parproc naming:** never use `error`, `exception`, `warning`, `notice`, `deprecated` in test names — use `failure`, `raises`, `invalid`, `bad_request`, `caution`, `legacy`
- **Lib rules:** `libs/*` never import `apps.*`; mocks live under `libs/clients/...`
- **Function documentation:** every new/changed function/method gets a brief docstring per [`AGENTS.md`](../../AGENTS.md)
- **No compatibility re-exports:** update imports to the canonical module; delete replaced files
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final below)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts

---

## File structure

```
olib/
  py/
    eval/
      __init__.py                 # NEW — public exports
      types.py                    # NEW — Sample, Score, RunPartition, protocols
      log.py                      # NEW — EventLogWriter
      matrix.py                   # NEW — expand suite × models
      runner.py                   # NEW — run_matrix orchestration
      report.py                   # NEW — text score table
      tests/
        __init__.py               # NEW
        test_log.py               # NEW
        test_matrix.py            # NEW
        test_runner.py            # NEW
    cli/run/templates/
      eval_.py                    # NEW — @eval_cmd decorator → orunr eval
      # wire into consumer Config via decorator (chief config.py)

backend/
  apps/runner/
    hooks.py                      # NEW — HookSet + safe invoke
    loop.py                       # MODIFY — register/fire hooks
    tests/
      test_hooks.py               # NEW
      test_loop.py                # MODIFY if needed for hook smoke
    tool_wiring path:
  apps/agents/
    tool_wiring.py                # MODIFY — client_factories dict
    tests/test_tool_wiring.py     # MODIFY
  libs/clients/
    gmail/
      protocol.py                 # NEW — GmailClientProtocol
      mock.py                     # NEW — MockGmailClient
      client.py                   # MODIFY — assert/implement protocol (optional typing)
      tests/test_mock.py          # NEW
    clickup/
      protocol.py                 # NEW
      mock.py                     # NEW
      tests/test_mock.py          # NEW
  libs/usecases/                  # NEW package — thin setup (not a runner)
    __init__.py
    setup.py                      # load spec, memory backend, inject mocks, hooks
    observability.py              # SessionRunner hooks → terminal + EventLogWriter
    scenarios.py                  # load functional YAML → FakeProvider plan
    tests/
      test_setup.py
  libs/agent_specs/examples/
    inbox-triage-usecase.yaml     # NEW — minimal triage spec for tests (stand-in until spec 9)
evals/
  inbox/
    __init__.py                   # NEW — suite plugin entry
    scenarios/                    # NEW — eval-only YAML/JSON
      ambiguous-act-vs-read.yaml
    scorers.py                    # NEW
    runner.py                     # NEW — implements olib SampleRunner
config.py                         # MODIFY — @eval_cmd + eval_suites entry
```

---

## S1 — SessionRunner hooks

### Task 1: Hook registry + SessionRunner integration

**Files:**
- Create: `backend/apps/runner/hooks.py`
- Create: `backend/apps/runner/tests/test_hooks.py`
- Modify: `backend/apps/runner/loop.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/apps/runner/tests/test_hooks.py
from unittest.mock import MagicMock, patch

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookSet
from apps.runner.loop import SessionRunner
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from libs.agent_specs import load_example
from libs.providers.llm.base import StreamResult
from libs.providers.llm.fake_provider import FakeProvider
from olib.py.django.test.cases import OTestCase


class TestSessionRunnerHooks(OTestCase):
    def test_hooks_fire_for_generate_tool_and_run(self) -> None:
        """Hooks observe generate, tool call, and run lifecycle without altering control flow."""
        spec = load_example('clock-assistant').model_copy()
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        seen: list[str] = []
        hooks = HookSet(
            on_run_start=lambda: seen.append('run_start'),
            on_run_end=lambda: seen.append('run_end'),
            on_generate_start=lambda messages, tools: seen.append('gen_start'),
            on_generate_end=lambda result: seen.append('gen_end'),
            on_tool_call_start=lambda call: seen.append(f"tool_start:{call.get('name')}"),
            on_tool_call_end=lambda call, content: seen.append('tool_end'),
            on_event=lambda event: seen.append(f'event:{event.kind}'),
            on_status=lambda status: seen.append(f'status:{status}'),
        )
        runner.add_hook(hooks)
        tool_call = StreamResult(
            content='',
            tool_calls=[{'id': 'c1', 'name': 'clock.get_time', 'arguments': {}}],
        )
        follow = StreamResult(content='done')
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([tool_call, follow]),
        ):
            runner.run()
        self.assertIn('run_start', seen)
        self.assertIn('gen_start', seen)
        self.assertIn('gen_end', seen)
        self.assertTrue(any(s.startswith('tool_start:') for s in seen))
        self.assertIn('tool_end', seen)
        self.assertIn('run_end', seen)
        self.assertTrue(any(s.startswith('event:') for s in seen))

    def test_hook_failure_does_not_fail_session(self) -> None:
        """Observability hook exceptions are swallowed so the session still completes."""
        spec = load_example('clock-assistant').model_copy()
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)

        def boom() -> None:
            raise RuntimeError('hook boom')

        runner.add_hook(HookSet(on_run_start=boom))
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
        ):
            runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [e.kind for e in backend.events()]
        self.assertIn(AgentSessionEventKind.OUTPUT, kinds)
        self.assertNotIn(AgentSessionEventKind.FAILURE, kinds)
```

Adjust `clock.get_time` to a tool name that exists on the clock-assistant example (read the example YAML and use a real allowed tool, or use an unknown tool and still assert `tool_start` fires). Prefer a real tool if the example has one; otherwise assert `tool_start` with the unknown name and that TOOL_RESULT still records failure JSON.

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_hooks.py -v1
```

Expected: FAIL (HookSet / add_hook missing).

- [ ] **Step 3: Implement hooks module**

```python
# backend/apps/runner/hooks.py
"""SessionRunner observability hooks (side-effect free w.r.t. control flow)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apps.runner.backends.base import RecordedEvent
from libs.providers.llm.base import StreamResult

logger = logging.getLogger(__name__)


@dataclass
class HookSet:
    """Optional callbacks for SessionRunner lifecycle points."""

    on_run_start: Callable[[], None] | None = None
    on_run_end: Callable[[], None] | None = None
    on_generate_start: Callable[[list[dict[str, Any]], list[Any]], None] | None = None
    on_generate_end: Callable[[StreamResult], None] | None = None
    on_tool_call_start: Callable[[dict[str, Any]], None] | None = None
    on_tool_call_end: Callable[[dict[str, Any], str], None] | None = None
    on_event: Callable[[RecordedEvent], None] | None = None
    on_status: Callable[[str], None] | None = None


@dataclass
class HookRegistry:
    """Holds HookSets and invokes them safely (log + continue on failure)."""

    _hooks: list[HookSet] = field(default_factory=list)

    def add(self, hooks: HookSet) -> None:
        """Register one HookSet for subsequent SessionRunner calls."""
        self._hooks.append(hooks)

    def fire(self, name: str, *args: Any) -> None:
        """Call ``name`` on each HookSet; never raise to the runner."""
        for hooks in self._hooks:
            cb = getattr(hooks, name, None)
            if cb is None:
                continue
            try:
                cb(*args)
            except Exception:  # noqa: BLE001 — observability must not fail the session
                logger.exception('SessionRunner hook %s failed', name)
```

- [ ] **Step 4: Wire hooks into SessionRunner**

In `SessionRunner.__init__`, add `self.hooks = HookRegistry()`.

Add:

```python
def add_hook(self, hooks: HookSet) -> None:
    """Register observability hooks for this runner instance."""
    self.hooks.add(hooks)
```

In `run()`:
- first line after enter: `self.hooks.fire('on_run_start')`
- wrap the main body in `try`/`finally` with `self.hooks.fire('on_run_end')` in `finally`

Before `provider.collect(...)`: `self.hooks.fire('on_generate_start', messages, tool_definitions)`
After collect: `self.hooks.fire('on_generate_end', result)`

In `_handle_tool_call`, before invoke: `on_tool_call_start(call)`; after result string ready: `on_tool_call_end(call, result_content)`.

After every `append_event` + before/with `publish_event`: `self.hooks.fire('on_event', event)`.

Wrap `set_status` calls through a small helper:

```python
def _set_status(self, status: str) -> None:
    """Set backend status and notify hooks."""
    self.backend.set_status(status)
    self.hooks.fire('on_status', status)
```

Replace direct `set_status` usages in `loop.py` with `_set_status`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_hooks.py backend/apps/runner/tests/test_loop.py -v1
```

Expected: PASS

- [ ] **Step 6: Commit and sync**

```bash
git add backend/apps/runner/hooks.py backend/apps/runner/loop.py backend/apps/runner/tests/test_hooks.py
git commit -m "feat(runner): add SessionRunner observability hooks"
git fetch origin main && git rebase origin/main && git push
```

---

## S2 — olib eval library (EventLogWriter + matrix)

### Task 2: EventLogWriter + matrix helpers

**Files:** (all under olib submodule)
- Create: `olib/py/eval/__init__.py`, `types.py`, `log.py`, `matrix.py`, `runner.py`, `report.py`
- Create: `olib/py/eval/tests/test_log.py`, `test_matrix.py`, `test_runner.py`

- [ ] **Step 1: Write failing olib unit tests**

```python
# olib/py/eval/tests/test_log.py
import json
import tempfile
import unittest
from pathlib import Path

from olib.py.eval.log import EventLogWriter
from olib.py.eval.types import RunPartition


class TestEventLogWriter(unittest.TestCase):
    def test_writes_partitioned_jsonl(self) -> None:
        """Writer creates a path keyed by kind/suite/sample/model/run_id."""
        with tempfile.TemporaryDirectory() as tmp:
            writer = EventLogWriter(root=Path(tmp))
            part = RunPartition(
                kind='eval',
                suite='inbox',
                sample_id='spam-1',
                model='anthropic/claude-sonnet-4-6',
                run_id='runA',
            )
            path = writer.path_for(part)
            writer.append(part, {'type': 'event', 'kind': 'OUTPUT', 'payload': {'content': 'hi'}})
            writer.append(part, {'type': 'score', 'value': 1.0})
            self.assertTrue(path.is_file())
            lines = path.read_text(encoding='utf-8').strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])['kind'], 'OUTPUT')
            # path must contain partition segments for easy grep
            text = str(path)
            self.assertIn('eval', text)
            self.assertIn('inbox', text)
            self.assertIn('spam-1', text)
```

```python
# olib/py/eval/tests/test_matrix.py
import unittest

from olib.py.eval.matrix import expand_matrix
from olib.py.eval.types import Sample


class TestExpandMatrix(unittest.TestCase):
    def test_cartesian_samples_by_models(self) -> None:
        samples = [Sample(id='a', payload={}), Sample(id='b', payload={})]
        cells = expand_matrix(samples, models=['m1', 'm2'])
        self.assertEqual([(c.sample.id, c.model) for c in cells], [('a', 'm1'), ('a', 'm2'), ('b', 'm1'), ('b', 'm2')])
```

- [ ] **Step 2: Run to verify fail**

```bash
./olib/scripts/orunr py test olib/py/eval/tests -v1
```

Expected: FAIL (module missing).

- [ ] **Step 3: Implement types + log + matrix + runner + report**

```python
# olib/py/eval/types.py
"""Shared types and protocols for project eval plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RunPartition:
    """Keys used to locate one run's event log slice."""

    kind: str  # 'functional' | 'eval'
    suite: str
    sample_id: str
    model: str
    run_id: str


@dataclass
class Sample:
    """One eval/functional scenario identity + opaque payload for the project runner."""

    id: str
    payload: dict[str, Any]


@dataclass
class Score:
    """Soft score for one sample × model cell."""

    value: float
    axes: dict[str, float] = field(default_factory=dict)
    notes: str = ''


@dataclass
class MatrixCell:
    sample: Sample
    model: str


@dataclass
class CellResult:
    cell: MatrixCell
    score: Score | None
    failure: str | None = None


class SampleRunner(Protocol):
    """Project plugin: execute one sample under one model id."""

    def run_sample(self, sample: Sample, *, model: str, partition: RunPartition) -> Score:
        ...


class Suite(Protocol):
    """Project plugin: list samples for a named suite."""

    @property
    def name(self) -> str: ...

    def samples(self) -> list[Sample]: ...
```

```python
# olib/py/eval/log.py
"""Partitioned JSONL event-log writer for usecase tests and evals."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from olib.py.eval.types import RunPartition

_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


def _seg(value: str) -> str:
    """Sanitize one path segment for filesystem safety."""
    cleaned = _SAFE.sub('_', value.strip()) or 'unknown'
    return cleaned[:120]


class EventLogWriter:
    """Append JSON lines under ``root/kind/suite/sample/model/run_id.jsonl``."""

    def __init__(self, *, root: Path) -> None:
        self.root = root

    def path_for(self, partition: RunPartition) -> Path:
        """Return the log file path for *partition* (directories created on write)."""
        return (
            self.root
            / _seg(partition.kind)
            / _seg(partition.suite)
            / _seg(partition.sample_id)
            / _seg(partition.model)
            / f'{_seg(partition.run_id)}.jsonl'
        )

    def append(self, partition: RunPartition, record: dict[str, Any]) -> Path:
        """Append one JSON object as a line; create parents as needed."""
        path = self.path_for(partition)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, default=str) + '\n')
        return path
```

```python
# olib/py/eval/matrix.py
"""Cartesian expansion of samples × models."""

from __future__ import annotations

from olib.py.eval.types import MatrixCell, Sample


def expand_matrix(samples: list[Sample], models: list[str]) -> list[MatrixCell]:
    """Return one MatrixCell per (sample, model) pair in stable order."""
    return [MatrixCell(sample=s, model=m) for s in samples for m in models]
```

```python
# olib/py/eval/runner.py
"""Run an eval matrix via a project SampleRunner."""

from __future__ import annotations

import uuid

from olib.py.eval.log import EventLogWriter
from olib.py.eval.matrix import expand_matrix
from olib.py.eval.types import CellResult, RunPartition, Sample, SampleRunner, Score, Suite


def run_matrix(
    suite: Suite,
    *,
    models: list[str],
    sample_runner: SampleRunner,
    log_writer: EventLogWriter,
    kind: str = 'eval',
    run_id: str | None = None,
) -> list[CellResult]:
    """Execute suite samples × models; record failures without aborting the matrix."""
    rid = run_id or uuid.uuid4().hex[:12]
    results: list[CellResult] = []
    for cell in expand_matrix(suite.samples(), models):
        part = RunPartition(
            kind=kind,
            suite=suite.name,
            sample_id=cell.sample.id,
            model=cell.model,
            run_id=rid,
        )
        try:
            score = sample_runner.run_sample(cell.sample, model=cell.model, partition=part)
            log_writer.append(part, {'type': 'score', 'value': score.value, 'axes': score.axes, 'notes': score.notes})
            results.append(CellResult(cell=cell, score=score))
        except Exception as exc:  # noqa: BLE001 — continue matrix
            msg = str(exc)
            log_writer.append(part, {'type': 'failure', 'message': msg})
            results.append(CellResult(cell=cell, score=None, failure=msg))
    return results
```

```python
# olib/py/eval/report.py
"""Plain-text score table for eval matrix results."""

from __future__ import annotations

from olib.py.eval.types import CellResult


def format_score_table(results: list[CellResult]) -> str:
    """Render a simple sample × model score table."""
    lines = ['sample\tmodel\tscore\tnotes']
    for r in results:
        if r.failure:
            lines.append(f'{r.cell.sample.id}\t{r.cell.model}\tFAIL\t{r.failure}')
        else:
            assert r.score is not None
            lines.append(f'{r.cell.sample.id}\t{r.cell.model}\t{r.score.value:.3f}\t{r.score.notes}')
    return '\n'.join(lines) + '\n'
```

Export the public API from `olib/py/eval/__init__.py`.

Add `test_runner.py` that uses a tiny fake `Suite`/`SampleRunner` and asserts two cells + score lines in the log.

- [ ] **Step 4: Run olib tests**

```bash
./olib/scripts/orunr py test olib/py/eval/tests -v1
```

Expected: PASS

- [ ] **Step 5: Commit inside olib submodule, then bump pointer in chief**

```bash
cd olib
git checkout -b feat/2026-07-11-usecase-tests-evals 2>/dev/null || git checkout feat/2026-07-11-usecase-tests-evals
git add py/eval
git commit -m "feat(eval): add EventLogWriter and matrix runner"
# push olib remote as appropriate for this environment
cd ..
git add olib
git commit -m "chore: bump olib for eval library"
git fetch origin main && git rebase origin/main && git push
```

---

## S3 — `orunr eval` CLI

### Task 3: Eval click group + config discovery

**Files:**
- Create: `olib/py/cli/run/templates/eval_.py`
- Modify: `config.py` (chief) to apply `@eval_cmd()` and declare suite entrypoints
- Create/modify: olib CLI tests under `olib/py/cli/run/tests/test_eval.py`

**Config contract (locked):**

```python
# on project Config class:
eval_suites: dict[str, str] = {
    'inbox': 'evals.inbox:get_suite',  # import path → callable () -> Suite
}
eval_sample_runner: str = 'evals.inbox:get_sample_runner'  # () -> SampleRunner
eval_log_root: str = '.output/usecase-logs'
```

- [ ] **Step 1: Implement `@eval_cmd` decorator** following `django()` / `redis()` pattern:
  - `prep_config(cls)`
  - append `('eval', eval_group)` to `cls.meta.commandGroups`
  - mark `list`, `run`, `report` with `@readonly_safe`
  - `run` options: `--suite` (repeatable), `--model` (repeatable), `--allow-skip` (default **False** — missing API key / plugin import fails the command), `--run-id`
  - Resolve suite/runner via `importlib` from Config attributes
  - Default log root `.output/usecase-logs` (already gitignored via `.output/`)
  - On missing models list: fail with message requiring at least one `--model`
  - Print `format_score_table(results)` to stdout
  - Exit code 1 if any cell has `failure` **or** if infrastructure raises; exit 0 when all cells scored (even with score 0)

- [ ] **Step 2: Wire chief `config.py`**

```python
from olib.py.cli.run.templates.eval_ import eval_cmd

@eval_cmd()
# keep existing decorators
class Config:
    ...
    eval_suites = {'inbox': 'evals.inbox:get_suite'}
    eval_sample_runner = 'evals.inbox:get_sample_runner'
    eval_log_root = '.output/usecase-logs'
```

Until S7 lands, `orunr eval list` may fail importing suites — acceptable if S3 tests use a temp Config with a fake suite module. Prefer adding a minimal stub suite in S3 tests only (not production path).

- [ ] **Step 3: CLI unit test** with a fake Config + fake suite module (pattern from `olib/py/cli/run/tests/test_cli.py`).

- [ ] **Step 4: Commit olib + chief**

```bash
# olib commit for eval_.py + tests
# chief: config.py + submodule bump
git commit -m "feat(eval): add orunr eval CLI group"
git fetch origin main && git rebase origin/main && git push
```

---

## S4 — Gmail/ClickUp Protocols + mocks

### Task 4: Protocols and in-memory mocks

**Files:**
- Create: `backend/libs/clients/gmail/protocol.py`, `mock.py`, `tests/test_mock.py`
- Create: `backend/libs/clients/clickup/protocol.py`, `mock.py`, `tests/test_mock.py`

- [ ] **Step 1: Define protocols** matching methods tools actually call (from `GmailTool` / `ClickUpTool` dispatch), e.g. Gmail: `list_messages`, `get_message`, `list_labels`, `ensure_label_ids`, `modify_labels`, `archive`, `report_spam`, `get_attachment`, … ClickUp: `list_spaces`, `list_lists`, `list_tasks`, `get_task`, `create_task`, `update_task`, `create_comment`, …

Use `typing.Protocol` with the same signatures as the real clients.

- [ ] **Step 2: Implement `MockGmailClient` / `MockClickUpClient`**
  - Same constructor kwargs as real clients (`token_supplier`, `config`) — ignore token
  - Seed API: e.g. `seed_message(id, *, subject, snippet, label_ids, from_=...)`
  - Record mutations for asserts: `labeled`, `archived_ids`, `spam_ids`, `created_tasks`
  - `ensure_label_ids` creates synthetic ids for unknown names
  - `create_task` appends to `created_tasks` and returns `{id: ...}`

- [ ] **Step 3: Unit tests** for seed → label/spam/archive → assert recorded state; ClickUp create_task recorded.

- [ ] **Step 4: Run**

```bash
./olib/scripts/orunr py test backend/libs/clients/gmail/tests/test_mock.py backend/libs/clients/clickup/tests/test_mock.py -v1
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(clients): add Gmail and ClickUp protocols and mocks"
git fetch origin main && git rebase origin/main && git push
```

---

## S5 — Client factory injection + usecase setup helpers

### Task 5: Thread `client_factories` through tool wiring + SessionRunner

**Files:**
- Modify: `backend/apps/agents/tool_wiring.py`
- Modify: `backend/apps/runner/loop.py` — accept optional `client_factories: dict[str, Callable[..., Any]] | None = None` and pass into `build_bound_tools`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Failing test** — `build_bound_tools(..., client_factories={'gmail': lambda **kw: mock})` causes gmail invoke to hit mock (assert mock method called / no real client).

- [ ] **Step 2: Implementation**

In `bind_tool_invoke`, add `client_factory: Callable[..., Any] | None = None`. When `bind` exists and `token_supplier is not None`, call:

```python
kwargs: dict[str, Any] = {'token_supplier': token_supplier, 'config': config}
if client_factory is not None:
    kwargs['client_factory'] = client_factory
return cast(..., bind(**kwargs))
```

In `build_bound_tools`, add `client_factories: dict[str, Callable[..., Any]] | None = None` and pass `client_factories.get(inst.type) if client_factories else None`.

`SessionRunner.__init__(..., client_factories=None)` stores and passes to `build_bound_tools`.

- [ ] **Step 3: Tests pass + commit**

```bash
git commit -m "feat(agents): inject client_factories into tool binding"
git fetch origin main && git rebase origin/main && git push
```

### Task 6: Usecase setup + observability hooks helper

**Files:**
- Create: `backend/libs/usecases/__init__.py`, `setup.py`, `observability.py`, `scenarios.py`
- Create: `backend/libs/usecases/tests/test_setup.py`
- Create: `backend/libs/agent_specs/examples/inbox-triage-usecase.yaml` (minimal gmail+clickup tools + system prompt; stand-in until spec 9)

- [ ] **Step 1: `observability.py`** — build a `HookSet` that:
  - prints short terminal lines for generate/tool/event
  - appends JSON records via `EventLogWriter` for the given `RunPartition`

- [ ] **Step 2: `setup.py`**

```python
def build_inbox_runner(
    *,
    spec: AgentConfigSpec,
    gmail: MockGmailClient,
    clickup: MockClickUpClient,
    provider: LLMProvider,
    partition: RunPartition,
    log_writer: EventLogWriter,
    prompt: str,
) -> SessionRunner:
    """Memory backend + mock clients + hooks; caller patches make_provider or passes provider via patch in test."""
    ...
```

Pattern for provider: keep using `unittest.mock.patch('apps.runner.loop.make_provider', return_value=provider)` in tests/evals (existing seam). Document that in the helper docstring — helper returns `(backend, runner)` after registering hooks and pushing mailbox chat `prompt`.

- [ ] **Step 3: `scenarios.py`** — load functional YAML with:
  - `id`, `prompt`, `seed_gmail`, `seed_clickup`, `fake_responses` (list of `{content, tool_calls}`), `expect` (labels/spam/tasks)
  - builder: `FakeProvider.for_responses([...])`

- [ ] **Step 4: Unit test** that runs one tiny inline scenario (not full inbox) with clock or gmail mock — proves hooks write a log file under a temp root.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(usecases): setup helpers and observability hooks"
git fetch origin main && git rebase origin/main && git push
```

---

## S6 — Functional inbox usecase tests

### Task 7: Functional scenarios + Django tests

**Files:**
- Create: `backend/apps/runner/tests/usecases/scenarios/functional/spam_route.yaml`
- Create: `backend/apps/runner/tests/usecases/scenarios/functional/todo_to_clickup.yaml`
- Create: `backend/apps/runner/tests/usecases/test_inbox_functional.py`

- [ ] **Step 1: Write scenarios**
  - `spam_route`: seed obvious spam message; FakeProvider plan calls gmail label + report_spam; expect mock spam/label state
  - `todo_to_clickup`: seed actionable email; plan creates ClickUp task + gmail label; expect `created_tasks` and labels

Use `inbox-triage-usecase.yaml` as the agent spec. Prompt text can be the triage trigger string plus message id.

- [ ] **Step 2: Test class** loads each scenario, builds mocks, FakeProvider, `EventLogWriter` under `tempfile` or `.output/usecase-logs/functional/...`, runs `SessionRunner`, hard-asserts mock state, asserts log file non-empty.

- [ ] **Step 3: Run**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/usecases -v1
./olib/scripts/orunr py test-all
```

- [ ] **Step 4: Commit**

```bash
git commit -m "test(usecases): inbox functional routing with FakeProvider"
git fetch origin main && git rebase origin/main && git push
```

---

## S7 — Inbox eval plugin + matrix smoke

### Task 8: Eval suite, scorers, sample runner

**Files:**
- Create: `evals/inbox/__init__.py`, `runner.py`, `scorers.py`
- Create: `evals/inbox/scenarios/ambiguous-act-vs-read.yaml` (and one more edge-case file)
- Ensure `config.py` entrypoints resolve (repo root on `PYTHONPATH` via orun — verify import works from `orunr eval`)

- [ ] **Step 1: Scorer** compares mock end-state to `expect` in the scenario: exact label match → 1.0; partial axes (`label`, `clickup`, `spam`) average for `Score.value`.

- [ ] **Step 2: `InboxSampleRunner.run_sample`**
  - Load scenario payload
  - Seed mocks
  - Build real provider via `make_provider` for `model` string (parse `provider/model` or require Config LLM defaults — **locked:** model CLI flag is `provider/model` e.g. `anthropic/claude-sonnet-4-6`; map to ProviderLLMConfig)
  - Register observability hooks with partition
  - Run SessionRunner
  - Score from mocks
  - On missing credentials: raise clear `RuntimeError('Missing credentials for ...')` — CLI fails unless `--allow-skip` (when allow-skip, record failure cell and continue)

- [ ] **Step 3: `get_suite` / `get_sample_runner` entrypoints**

- [ ] **Step 4: Manual smoke** (requires API key in env):

```bash
./olib/scripts/orunr eval list
./olib/scripts/orunr eval run --suite inbox --model anthropic/claude-sonnet-4-6
```

Expected: score table printed; logs under `.output/usecase-logs/eval/inbox/...`

If no key available in the agent environment, run with `--allow-skip` once to prove CLI path, and note in commit message that live matrix needs keys.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(evals): inbox suite and orunr eval matrix smoke"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 9: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`**. Do not fix findings unless the user asks — summarize in chat and in the review file.

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

- `{DESCRIPTION}` — SessionRunner hooks, olib eval CLI/library, client mocks, inbox functional + eval suites
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-11-usecase-tests-evals/` design + plan
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Write `docs/specs/2026-07-11-usecase-tests-evals/2026-07-11-usecase-tests-evals-review.md` per `review-file-template.md`. Summarize in chat.

- [ ] **Step 5: Track feedback**

Update **Status** in `*-review.md` to Fixed / Rejected as the user directs.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. Do not check epic boxes unless the user explicitly approves after review.

---

## Out of scope

- Inspect AI / Promptfoo / DeepEval integration
- Source → queue → dispatch full-path helpers (optional follow-up; not required for inbox routing usecases)
- Spec 9 production triage prompt/policy (minimal YAML stand-in only)
- Production APM
- Mock selection via agent YAML

---

## Spec coverage checklist

| Design section | Plan tasks |
|----------------|------------|
| SessionRunner only / MemorySessionBackend | S5–S6 |
| No UsecaseHarness runner | S5 helpers only |
| SessionRunner hooks | S1 |
| Event log + terminal via hooks | S2 + S5 observability |
| olib eval lib + CLI | S2–S3 |
| Protocols + mocks | S4 |
| client_factories injection | S5 Task 5 |
| Functional FakeProvider scenarios | S6 |
| Eval matrix + scorers | S7 |
| No mock in YAML | all stages |
| Code review | S_final |

---

## References

- Design: [`2026-07-11-usecase-tests-evals-design.md`](./2026-07-11-usecase-tests-evals-design.md)
- Epic: [`docs/epics/2026-07-03-inbox-cleanup.md`](../../epics/2026-07-03-inbox-cleanup.md)
- Existing seams: `SessionRunner`, `MemorySessionBackend`, `FakeProvider`, `GmailTool.bind(client_factory=…)`
