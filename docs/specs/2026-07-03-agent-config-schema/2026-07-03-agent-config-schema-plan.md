# Agent config schema extensions Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **2 of 9** · Item: **Agent config schema extensions**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 below before any code change** — checkout the feature branch, then create `-revision.md`. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `AgentConfigSpec` with versioned tool instances and `credential_ref`, add a load-time spec migration framework (0→1), wire tools at session start with instance-scoped provider names, and persist saves at the latest schema version as new config rows.

**Architecture:** Pydantic models describe **current version only** (`schema_version=1`). `spec_migrations/` upgrades stored v0 JSON on every load via `load_spec_dict()`; saves write `spec_version` + JSON at v1 as a **new** `AgentConfig` row. Runner uses `apps.agents.tool_wiring.build_bound_tools()` keyed by instance id; wire names are `{instance_id}__{function}`.

**Tech Stack:** Django 5.2, Pydantic v2, existing `libs/tools` registry, `apps.keys.make_secret_supplier`.

**Branch:** `feat/2026-07-03-agent-config-schema`

**Design spec:** [`2026-07-03-agent-config-schema-design.md`](./2026-07-03-agent-config-schema-design.md)
**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md) (spec migrations checklist)
**Superpowers policy:** [`olib/docs/specs/01-superpowers/01-superpowers.spec.md`](../../olib/docs/specs/01-superpowers/01-superpowers.spec.md)

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

From the `chief/` repo root:

```bash
git checkout feat/2026-07-03-agent-config-schema || git checkout -b feat/2026-07-03-agent-config-schema
git branch --show-current   # must print feat/2026-07-03-agent-config-schema
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Create review template**

Create `docs/specs/2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-revision.md` from the **Revision template** in [`olib/docs/specs/01-superpowers/01-superpowers.spec.md`](../../olib/docs/specs/01-superpowers/01-superpowers.spec.md). Leave review sections empty.

- [ ] **Step 0c: Commit pre-implementation artifacts (if uncommitted)**

```bash
git add docs/specs/2026-07-03-agent-config-schema/
git commit -m "docs(agents): add agent config schema design and plan"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Skip 0c if already committed on the feature branch.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all`
- Django migrations: `./olib/scripts/orunr django manage makemigrations agents` (never hand-write migration files)
- Test base: `olib.py.django.test.cases.OTestCase` / `OTransactionTestCase`
- Avoid parproc keywords in test names (`error`, `exception`, …)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts

---

## Target file map

```
backend/apps/agents/
  spec.py                              # AGENT_CONFIG_SPEC_VERSION, ToolInstance, schema_version
  spec_migrations/
    __init__.py                        # load_spec_dict, load_spec, detect_version, apply_upgrade_chain
    registry.py                        # discover migrations/, SpecMigration, chain validation
    exceptions.py                      # UnsupportedSpecVersionError, SpecMigrationError
    migrations/
      __init__.py
      001_tool_instances.py            # FROM_VERSION=0, TO_VERSION=1, upgrade()
  tool_wiring.py                       # BoundToolInstance, build_bound_tools, bind_tool_invoke
  ingest.py                            # validate_spec_tools (instances), persist_agent_config
  models.py                            # + spec_version; get_spec() uses load_spec
  hardcoded.py                         # v1 HARDCODED_SPEC
  migrations/0002_agentconfig_spec_version.py   # DDL only
  tests/
    test_spec_migrations.py            # NEW
    test_spec.py                       # NEW (or fold into test_spec_migrations)
    test_ingest.py                     # update for ToolInstance
    test_tool_wiring.py                # expand (replace test_tools_wiring.py or merge)

backend/apps/runner/
  tool_definitions.py                  # ToolInstance + instance id wire names
  loop.py                              # bound_tools, instance-scoped allow/invoke/events
  spec_loader.py                       # load_spec_dict path
  llm_config.py                        # (no change if loop passes credential_ref)
  tests/
    test_spec_loader.py
    test_loop.py
    test_tool_definitions.py           # NEW

backend/libs/tools/
  base.py                              # + credential_type on Tool

backend/libs/providers/tests/
  test_tools_anthropic.py              # ToolInstance + instance id in wire name
```

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Credential ref field | `credential_ref` on LLM + tool instances |
| Spec versions | 0 = legacy; 1 = current after this spec |
| Migration files | `spec_migrations/migrations/NNN_{name}.py`; `001_tool_instances.py` = 0→1 |
| Load path | Always `load_spec_dict()` → upgrade chain → pydantic |
| Migration discovery | `@functools.cache` on registry — once per process |
| Save path | New `AgentConfig` row; `spec_version=1`; never UPDATE spec JSON in place |
| Bulk upgrade command | **No** |
| Wire names | `{instance_id}__{function}` |
| Credential tool tests | Register ephemeral test tool in test `setUp` via `register_tool` |

---

## S1 — Spec migration framework

### Task 1: Migration exceptions

**Files:**
- Create: `backend/apps/agents/spec_migrations/exceptions.py`
- Create: `backend/apps/agents/spec_migrations/__init__.py` (empty re-exports for now)
- Create: `backend/apps/agents/spec_migrations/migrations/__init__.py`

- [ ] **Step 1: Write exceptions**

```python
# backend/apps/agents/spec_migrations/exceptions.py
class UnsupportedSpecVersionError(ValueError):
    """Stored spec version is newer than this Chief build supports."""

class SpecMigrationError(ValueError):
    """A spec migration step failed."""
```

- [ ] **Step 2: Commit**

```bash
git add backend/apps/agents/spec_migrations/
git commit -m "feat(agents): add spec migration exception types"
git fetch origin main && git rebase origin main && git push
```

---

### Task 2: `001_tool_instances` migration step

**Files:**
- Create: `backend/apps/agents/spec_migrations/migrations/001_tool_instances.py`
- Create: `backend/apps/agents/tests/test_spec_migrations.py`

- [ ] **Step 1: Write failing tests for 0→1 upgrade**

```python
# backend/apps/agents/tests/test_spec_migrations.py
from apps.agents.spec_migrations.migrations import tool_instances as mig001

from olib.py.django.test.cases import OTestCase

V0_CLOCK_SPEC = {
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [{'tool': 'clock', 'allow': ['now']}],
}


class TestMigration001ToolInstances(OTestCase):
    def test_module_versions(self) -> None:
        self.assertEqual(mig001.FROM_VERSION, 0)
        self.assertEqual(mig001.TO_VERSION, 1)

    def test_upgrade_maps_tool_to_instance(self) -> None:
        out = mig001.upgrade(dict(V0_CLOCK_SPEC))
        self.assertEqual(out['schema_version'], 1)
        self.assertEqual(len(out['tools']), 1)
        inst = out['tools'][0]
        self.assertEqual(inst['id'], 'clock')
        self.assertEqual(inst['type'], 'clock')
        self.assertEqual(inst['allow'], ['now'])
        self.assertNotIn('tool', inst)

    def test_upgrade_rejects_duplicate_tool_names(self) -> None:
        raw = dict(V0_CLOCK_SPEC)
        raw['tools'] = [
            {'tool': 'clock', 'allow': ['now']},
            {'tool': 'clock', 'allow': ['now']},
        ]
        with self.assertRaises(SpecMigrationError):
            mig001.upgrade(raw)
```

Add `from apps.agents.spec_migrations.exceptions import SpecMigrationError` at top.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec_migrations.py -v`
Expected: import or `upgrade` not defined

- [ ] **Step 3: Implement migration**

```python
# backend/apps/agents/spec_migrations/migrations/001_tool_instances.py
from __future__ import annotations

from apps.agents.spec_migrations.exceptions import SpecMigrationError

FROM_VERSION = 0
TO_VERSION = 1


def upgrade(raw: dict) -> dict:
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    tools_in = list(out.get('tools') or [])
    seen_ids: set[str] = set()
    tools_out: list[dict] = []
    for entry in tools_in:
        tool_name = entry.get('tool')
        if not tool_name:
            raise SpecMigrationError('v0 tool entry missing tool name')
        if tool_name in seen_ids:
            raise SpecMigrationError(f"duplicate tool {tool_name!r} — add explicit instance ids in v1")
        seen_ids.add(tool_name)
        tools_out.append(
            {
                'id': tool_name,
                'type': tool_name,
                'allow': list(entry.get('allow') or ['*']),
                'deny': list(entry.get('deny') or []),
            }
        )
    out['tools'] = tools_out
    return out
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec_migrations.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/agents/spec_migrations/migrations/001_tool_instances.py backend/apps/agents/tests/test_spec_migrations.py
git commit -m "feat(agents): add 001_tool_instances spec migration"
git fetch origin main && git rebase origin main && git push
```

---

### Task 3: Registry + `load_spec_dict`

**Files:**
- Create: `backend/apps/agents/spec_migrations/registry.py`
- Modify: `backend/apps/agents/spec_migrations/__init__.py`
- Modify: `backend/apps/agents/tests/test_spec_migrations.py`

- [ ] **Step 1: Write failing tests for registry and load**

Append to `test_spec_migrations.py`:

```python
from apps.agents.spec_migrations import detect_version, load_spec_dict
from apps.agents.spec_migrations.exceptions import UnsupportedSpecVersionError
from apps.agents.spec_migrations.registry import get_spec_migrations, latest_spec_version


class TestSpecMigrationRegistry(OTestCase):
    def test_registry_has_contiguous_chain_starting_at_zero(self) -> None:
        steps = get_spec_migrations()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].from_version, 0)
        self.assertEqual(steps[0].to_version, 1)
        self.assertEqual(latest_spec_version(), 1)

    def test_detect_version_legacy_shape(self) -> None:
        self.assertEqual(detect_version(V0_CLOCK_SPEC), 0)

    def test_detect_version_from_schema_version_field(self) -> None:
        raw = {'schema_version': 1, 'tools': []}
        self.assertEqual(detect_version(raw), 1)

    def test_load_spec_dict_upgrades_v0(self) -> None:
        out = load_spec_dict(V0_CLOCK_SPEC, stored_version=0)
        self.assertEqual(out['schema_version'], 1)
        self.assertEqual(out['tools'][0]['id'], 'clock')

    def test_load_spec_dict_rejects_future_version(self) -> None:
        with self.assertRaises(UnsupportedSpecVersionError):
            load_spec_dict({'schema_version': 99, 'tools': []}, stored_version=99)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec_migrations.py -v`

- [ ] **Step 3: Implement registry**

```python
# backend/apps/agents/spec_migrations/registry.py
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from apps.agents.spec_migrations.exceptions import SpecMigrationError

_MIGRATION_RE = re.compile(r'^(\d{3})_(.+)\.py$')


@dataclass(frozen=True)
class SpecMigration:
    from_version: int
    to_version: int
    upgrade: Callable[[dict], dict]
    module_name: str


def _discover_migrations() -> tuple[SpecMigration, ...]:
    migrations_pkg = importlib.import_module('apps.agents.spec_migrations.migrations')
    pkg_path = Path(migrations_pkg.__file__).parent
    steps: list[SpecMigration] = []
    for info in sorted(pkgutil.iter_modules([str(pkg_path)])):
        match = _MIGRATION_RE.match(f'{info.name}.py')
        if not match:
            continue
        module = importlib.import_module(f'apps.agents.spec_migrations.migrations.{info.name}')
        expected_to = int(match.group(1))
        from_v = int(module.FROM_VERSION)
        to_v = int(module.TO_VERSION)
        if to_v != expected_to:
            raise SpecMigrationError(
                f'migration {info.name}: filename prefix {expected_to} != TO_VERSION {to_v}'
            )
        steps.append(
            SpecMigration(
                from_version=from_v,
                to_version=to_v,
                upgrade=module.upgrade,
                module_name=info.name,
            )
        )
    steps.sort(key=lambda s: s.from_version)
    expected = 0
    for step in steps:
        if step.from_version != expected:
            raise SpecMigrationError(
                f'migration gap: expected from_version {expected}, got {step.from_version} ({step.module_name})'
            )
        expected = step.to_version
    return tuple(steps)


def _discover_migrations() -> tuple[SpecMigration, ...]:
    ...  # importlib scan of migrations/ — see design doc


@functools.cache
def _cached_migrations() -> tuple[SpecMigration, ...]:
    return _discover_migrations()


def get_spec_migrations() -> tuple[SpecMigration, ...]:
    return _cached_migrations()


def latest_spec_version() -> int:
    steps = get_spec_migrations()
    if not steps:
        return 0
    return steps[-1].to_version
```

- [ ] **Step 4: Implement `__init__.py` load helpers**

```python
# backend/apps/agents/spec_migrations/__init__.py
from __future__ import annotations

from apps.agents.spec_migrations.exceptions import SpecMigrationError, UnsupportedSpecVersionError
from apps.agents.spec_migrations.registry import get_spec_migrations, latest_spec_version


def detect_version(raw: dict) -> int:
    if 'schema_version' in raw:
        return int(raw['schema_version'])
    tools = raw.get('tools') or []
    if tools and isinstance(tools[0], dict) and 'tool' in tools[0]:
        return 0
    return 0


def apply_upgrade_chain(raw: dict, *, from_version: int) -> dict:
    current = dict(raw)
    version = from_version
    for step in get_spec_migrations():
        if step.from_version < version:
            continue
        if step.from_version != version:
            raise SpecMigrationError(f'no migration from version {version}')
        try:
            current = step.upgrade(current)
        except SpecMigrationError:
            raise
        except Exception as exc:
            raise SpecMigrationError(f'migration {step.module_name} failed: {exc}') from exc
        version = step.to_version
    return current


def load_spec_dict(raw: dict, *, stored_version: int | None = None) -> dict:
    version = stored_version if stored_version is not None else detect_version(raw)
    latest = latest_spec_version()
    if version > latest:
        raise UnsupportedSpecVersionError(
            f'spec version {version} requires a newer Chief (supports up to {latest})'
        )
    return apply_upgrade_chain(raw, from_version=version)


__all__ = [
    'UnsupportedSpecVersionError',
    'SpecMigrationError',
    'detect_version',
    'load_spec_dict',
    'latest_spec_version',
]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec_migrations.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/agents/spec_migrations/
git commit -m "feat(agents): add spec migration registry and load_spec_dict"
git fetch origin main && git rebase origin/main && git push
```

---

## S2 — Pydantic schema v1

### Task 4: `ToolInstance` and `AgentConfigSpec`

**Files:**
- Modify: `backend/apps/agents/spec.py`
- Create: `backend/apps/agents/tests/test_spec.py`

- [ ] **Step 1: Write failing schema tests**

```python
# backend/apps/agents/tests/test_spec.py
from pydantic import ValidationError

from apps.agents.spec import AGENT_CONFIG_SPEC_VERSION, AgentConfigSpec, LLMSpec, ToolInstance, TriggerSpec

from olib.py.django.test.cases import OTestCase


class TestAgentConfigSpec(OTestCase):
    def test_current_schema_version_constant(self) -> None:
        self.assertEqual(AGENT_CONFIG_SPEC_VERSION, 1)

    def test_tool_instance_requires_id_and_type(self) -> None:
        inst = ToolInstance(id='clock', type='clock', allow=['now'])
        self.assertEqual(inst.type, 'clock')

    def test_duplicate_instance_ids_rejected_at_spec_level(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec(
                schema_version=1,
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hi',
                tools=[
                    ToolInstance(id='a', type='clock', allow=['now']),
                    ToolInstance(id='a', type='clock', allow=['now']),
                ],
            )
```

(Duplicate-id validation may live in ingest instead of pydantic — if so, adjust test to ingest in S4. Prefer pydantic `model_validator` on `AgentConfigSpec`.)

- [ ] **Step 2: Run tests — expect FAIL**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v`

- [ ] **Step 3: Update `spec.py`**

Replace `ToolPermission` with:

```python
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

AGENT_CONFIG_SPEC_VERSION = 1

_INSTANCE_ID_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


class LLMSpec(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
    credential_ref: str | None = None


class TriggerSpec(BaseModel):
    name: str
    kind: Literal['schedule', 'manual', 'agent']
    cron: str | None = None


class ToolInstance(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    credential_ref: str | None = None
    allow: list[str] = ['*']
    deny: list[str] = []


class AgentConfigSpec(BaseModel):
    schema_version: Literal[1] = AGENT_CONFIG_SPEC_VERSION
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    triggers: list[TriggerSpec] = []
    tools: list[ToolInstance] = []

    @model_validator(mode='after')
    def _unique_instance_ids(self) -> AgentConfigSpec:
        ids = [t.id for t in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate tool instance id')
        return self
```

Remove `ToolPermission` entirely.

Add helper:

```python
def load_spec(raw: dict, *, stored_version: int | None = None) -> AgentConfigSpec:
    from apps.agents.spec_migrations import load_spec_dict
    return AgentConfigSpec.model_validate(load_spec_dict(raw, stored_version=stored_version))
```

- [ ] **Step 4: Wire `latest_spec_version()` sync**

In `spec.py`, set `AGENT_CONFIG_SPEC_VERSION = latest_spec_version()` from registry at import time **or** keep constant `1` and add a test that asserts `AGENT_CONFIG_SPEC_VERSION == latest_spec_version()`. Prefer explicit constant `1` plus registry test (already in Task 3).

- [ ] **Step 5: Run tests — expect PASS for test_spec.py**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v`
Note: other tests will fail until S3–S7 — that is expected mid-plan.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/agents/spec.py backend/apps/agents/tests/test_spec.py
git commit -m "feat(agents): add ToolInstance schema v1 and load_spec helper"
git fetch origin main && git rebase origin/main && git push
```

---

## S3 — `AgentConfig.spec_version` + model load path

### Task 5: DB column and `get_spec()`

**Files:**
- Modify: `backend/apps/agents/models.py`
- Modify: `backend/apps/agents/tests/test_spec_migrations.py` (model integration test)

- [ ] **Step 1: Write failing model test**

```python
from apps.agents.hardcoded import HARDCODED_SPEC
from apps.agents.models import Agent, AgentConfig
from apps.agents.spec import AGENT_CONFIG_SPEC_VERSION
from django.contrib.auth import get_user_model

# in test_spec_migrations.py or new test_models.py
class TestAgentConfigGetSpec(OTestCase):
    def test_get_spec_upgrades_v0_row(self) -> None:
        user = get_user_model().objects.create_user(username='spec-load', password='x')
        agent = Agent.objects.create(user=user, identifier='a1')
        v0_json = {
            'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
            'system_prompt': 'hi',
            'tools': [{'tool': 'clock', 'allow': ['now']}],
        }
        config = AgentConfig.objects.create(agent=agent, spec_version=0, spec=v0_json)
        spec = config.get_spec()
        self.assertEqual(spec.schema_version, AGENT_CONFIG_SPEC_VERSION)
        self.assertEqual(spec.tools[0].id, 'clock')
```

- [ ] **Step 2: Add field and update `get_spec`**

```python
# models.py AgentConfig
spec_version = models.PositiveSmallIntegerField(default=0)

def get_spec(self) -> AgentConfigSpec:
    from apps.agents.spec import load_spec
    return load_spec(self.spec, stored_version=self.spec_version)
```

- [ ] **Step 3: Create migration**

Run: `./olib/scripts/orunr django manage makemigrations agents --name agentconfig_spec_version`
Verify migration adds `spec_version` with `default=0` only — **no RunPython**.

Run: `./olib/scripts/orunr django manage migrate`

- [ ] **Step 4: Run test — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec_migrations.py::TestAgentConfigGetSpec -v`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/agents/models.py backend/apps/agents/migrations/ backend/apps/agents/tests/
git commit -m "feat(agents): add AgentConfig.spec_version and upgrade-on-load get_spec"
git fetch origin main && git rebase origin/main && git push
```

---

## S4 — Ingest validation and save path

### Task 6: Instance validation + `persist_agent_config`

**Files:**
- Modify: `backend/apps/agents/ingest.py`
- Modify: `backend/apps/agents/tests/test_ingest.py`
- Modify: `backend/apps/agents/hardcoded.py`

- [ ] **Step 1: Update `HARDCODED_SPEC` to v1**

```python
HARDCODED_SPEC = AgentConfigSpec(
    schema_version=1,
    description='v0.1 demo agent',
    llm=LLMSpec(provider='openai', model='gpt-5.4-mini', temperature=0.7),
    system_prompt='...',
    triggers=[TriggerSpec(name='manual', kind='manual')],
    tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
)
```

- [ ] **Step 2: Rewrite ingest validation**

Replace `_validate_tool_permission` with `_validate_tool_instance`:

```python
def _validate_tool_instance(inst: ToolInstance) -> None:
    tool = get_tool(inst.type)
    if tool is None:
        raise IngestError(f'Unknown tool type {inst.type!r}')
    if inst.credential_ref and not getattr(tool, 'credential_type', None):
        raise IngestError(f"Tool {inst.type!r} does not accept credential_ref")
    known_functions = {fn.name for fn in tool.functions()}
    # ... same allow/deny checks as before, using inst.type
```

- [ ] **Step 3: Add `persist_agent_config`**

```python
def persist_agent_config(
    agent: Agent,
    spec: AgentConfigSpec,
    *,
    source_rev: str,
    dirty: bool = False,
) -> AgentConfig:
    validate_spec_tools(spec)
    if spec.schema_version != AGENT_CONFIG_SPEC_VERSION:
        raise IngestError('spec schema_version mismatch')
    spec_json = spec.model_dump(mode='json')
    config = AgentConfig.objects.create(
        agent=agent,
        source_rev=source_rev,
        dirty=dirty,
        spec_version=AGENT_CONFIG_SPEC_VERSION,
        spec=spec_json,
    )
    for trigger_spec in spec.triggers:
        Trigger.objects.create(
            agent=agent,
            agent_config=config,
            name=trigger_spec.name,
            kind=trigger_spec.kind,
            status=TriggerStatus.ACTIVE,
            spec=trigger_spec.model_dump(mode='json'),
        )
    agent.current_config = config
    agent.save(update_fields=['current_config'])
    return config
```

Refactor `create_agent_from_spec` to create `Agent` then call `persist_agent_config`.

- [ ] **Step 4: Update `test_ingest.py`**

Replace all `ToolPermission` with `ToolInstance`; add:

```python
def test_create_writes_spec_version_one(self) -> None:
    user = get_user_model().objects.create_user(username='sv', password='x')
    spec = HARDCODED_SPEC.model_copy()
    agent = create_agent_from_spec(user, spec, identifier='sv-agent')
    config = agent.current_config
    assert config is not None
    self.assertEqual(config.spec_version, 1)
    self.assertEqual(config.spec['schema_version'], 1)

def test_credential_ref_on_clock_rejected(self) -> None:
    spec = HARDCODED_SPEC.model_copy(
        update={'tools': [ToolInstance(id='clock', type='clock', credential_ref='x', allow=['now'])]},
    )
    with self.assertRaises(IngestError):
        validate_spec_tools(spec)
```

- [ ] **Step 5: Run ingest tests**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_ingest.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/agents/ingest.py backend/apps/agents/hardcoded.py backend/apps/agents/tests/test_ingest.py
git commit -m "feat(agents): ingest ToolInstance validation and persist spec_version on save"
git fetch origin main && git rebase origin/main && git push
```

---

## S5 — Tool registry + wiring

### Task 7: `credential_type` on `Tool`

**Files:**
- Modify: `backend/libs/tools/base.py`
- Modify: `backend/libs/tools/builtin.py`

- [ ] **Step 1: Add attribute**

```python
# base.py Tool
credential_type: str | None = None
```

`ClockTool` leaves default `None`.

- [ ] **Step 2: Commit**

```bash
git add backend/libs/tools/base.py backend/libs/tools/builtin.py
git commit -m "feat(tools): add credential_type on Tool base class"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 8: `tool_wiring.py`

**Files:**
- Create: `backend/apps/agents/tool_wiring.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py` (rename/expand from `test_tools_wiring.py`)

- [ ] **Step 1: Write failing wiring tests**

Register ephemeral credential tool in test:

```python
from apps.agents.spec import ToolInstance
from apps.agents.tool_wiring import BoundToolInstance, build_bound_tools
from libs.tools.base import Tool, ToolFunction
from libs.tools.registry import register_tool

class _EchoCredTool(Tool):
    name = 'echo_cred'
    credential_type = 'gmail'

    def functions(self) -> list[ToolFunction]:
        return [ToolFunction(name='ping', description='x', parameters={'type': 'object', 'properties': {}}, handler=self._ping)]

    def bind(self, *, token_supplier):
        def invoke(function: str, arguments: dict):
            if function != 'ping':
                raise ValueError(function)
            token = token_supplier()
            return {'token_set': token is not None}
        return invoke

    @staticmethod
    def _ping(**_kwargs):
        return 'ok'

class TestBuildBoundTools(OTestCase):
    def setUp(self) -> None:
        register_tool('echo_cred', _EchoCredTool())

    def test_clock_instance_invokes_without_credentials(self) -> None:
        instances = [ToolInstance(id='clock', type='clock', allow=['now'])]
        bound = build_bound_tools(instances, user_id=1)
        self.assertIn('clock', bound)
        result = bound['clock'].invoke('now', {})
        self.assertIsInstance(result, str)

    def test_credential_tool_uses_supplier(self) -> None:
        instances = [ToolInstance(id='gmail-a', type='echo_cred', allow=['ping'])]
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'tok'):
            bound = build_bound_tools(instances, user_id=1)
        out = bound['gmail-a'].invoke('ping', {})
        self.assertEqual(out, {'token_set': True})
```

- [ ] **Step 2: Implement `tool_wiring.py`**

```python
@dataclass(frozen=True)
class BoundToolInstance:
    instance_id: str
    tool_type: str
    invoke: Callable[[str, dict[str, Any]], Any]


def bind_tool_invoke(tool: Tool, *, token_supplier: Callable[[], str | None] | None) -> Callable[[str, dict], Any]:
    bind = getattr(tool, 'bind', None)
    if bind is not None and token_supplier is not None:
        return bind(token_supplier=token_supplier)
    return tool.invoke


def build_bound_tools(instances: list[ToolInstance], *, user_id: int | None) -> dict[str, BoundToolInstance]:
    bound: dict[str, BoundToolInstance] = {}
    for inst in instances:
        tool = get_tool(inst.type)
        assert tool is not None  # ingest validates
        supplier = None
        cred_type = getattr(tool, 'credential_type', None)
        if cred_type and user_id is not None:
            supplier = make_secret_supplier(user_id, name=inst.credential_ref, type=cred_type)
        invoke = bind_tool_invoke(tool, token_supplier=supplier)
        bound[inst.id] = BoundToolInstance(instance_id=inst.id, tool_type=inst.type, invoke=invoke)
    return bound
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_tool_wiring.py -v`

- [ ] **Step 4: Commit**

```bash
git add backend/apps/agents/tool_wiring.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "feat(agents): add tool instance wiring with credential suppliers"
git fetch origin main && git rebase origin/main && git push
```

---

## S6 — Runner integration

### Task 9: Tool definitions (instance wire names)

**Files:**
- Modify: `backend/apps/runner/tool_definitions.py`
- Create: `backend/apps/runner/tests/test_tool_definitions.py`
- Modify: `backend/libs/providers/tests/test_tools_anthropic.py`

- [ ] **Step 1: Write failing test**

```python
def test_two_instances_same_type_get_distinct_wire_names(self) -> None:
    instances = [
        ToolInstance(id='clock-a', type='clock', allow=['now']),
        ToolInstance(id='clock-b', type='clock', allow=['now']),
    ]
    defs = build_tool_definitions(instances, is_allowed=lambda *_a, **_k: True)
    names = {d.name for d in defs}
    self.assertEqual(names, {'clock-a__now', 'clock-b__now'})
```

- [ ] **Step 2: Update `build_tool_definitions`**

```python
def build_tool_definitions(
    instances: list[ToolInstance],
    *,
    is_allowed: Callable[..., bool],
) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            continue
        for fn in tool.functions():
            if not is_allowed(inst.id, fn.name, instance=inst):
                continue
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(inst.id, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    return definitions
```

Update `is_allowed` signature in loop to `(instance_id, function_name, *, instance=None)`.

- [ ] **Step 3: Update anthropic wire-name test** to use `ToolInstance(id='clock', type='clock', ...)`.

- [ ] **Step 4: Run tests**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/test_tool_definitions.py backend/libs/providers/tests/test_tools_anthropic.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/runner/tool_definitions.py backend/apps/runner/tests/ backend/libs/providers/tests/test_tools_anthropic.py
git commit -m "feat(runner): tool definitions use instance id wire names"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 10: Session loop — bound tools + events + LLM credential_ref

**Files:**
- Modify: `backend/apps/runner/loop.py`
- Modify: `backend/apps/runner/spec_loader.py`
- Modify: `backend/apps/runner/tests/test_loop.py`
- Modify: `backend/apps/runner/tests/test_spec_loader.py`
- Modify: `backend/apps/runner/tests/test_llm_config.py`

- [ ] **Step 1: Update `spec_loader`**

```python
from apps.agents.spec import load_spec

def load_agent_config_spec(raw: str) -> AgentConfigSpec:
    data = _parse_structured_text(raw)
    return load_spec(data)

def build_agent_config_spec(..., tools: list[ToolInstance] | None = None) -> AgentConfigSpec:
    return AgentConfigSpec(
        schema_version=AGENT_CONFIG_SPEC_VERSION,
        ...
        tools=tools or [ToolInstance(id='clock', type='clock', allow=['now'])],
    )
```

- [ ] **Step 2: Update `SessionRunner`**

In `__init__`:

```python
self.bound_tools = build_bound_tools(
    self.config_spec.tools,
    user_id=self.backend.user_id,
)
```

In `run()`, pass `credential_ref=self.config_spec.llm.credential_ref` to `provider_config_from_spec`.

Replace `_handle_tool_call`:

- Parse `(instance_id, function_name)` from wire name.
- `_is_allowed(instance_id, function_name)` finds `ToolInstance` by `id`.
- Look up `self.bound_tools[instance_id].invoke(function_name, arguments)`.
- Event payload: `instance_id`, `type` (tool type), `function` (not legacy `tool` = type only).

- [ ] **Step 3: Add loop test for tool call via instance id**

Use `FakeProvider` returning a tool call `clock__now` (instance id `clock`):

```python
def test_tool_call_invokes_bound_instance(self) -> None:
    backend = self._backend()
    backend.push_mailbox({'action': 'chat', 'content': 'time?'})
    tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': '1'}])
    follow_up = StreamResult(content='done')
    with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([tool_call, follow_up])):
        SessionRunner(backend).run()
    tool_results = [e for e in backend.events() if e.kind == AgentSessionEventKind.TOOL_RESULT]
    self.assertTrue(any('T' in e.payload.get('content', '') for e in tool_results))  # ISO timestamp
```

- [ ] **Step 4: Add llm_config test**

```python
def test_credential_ref_from_llm_spec(self) -> None:
    llm = LLMSpec(provider='openai', model='m', credential_ref='my-openai')
    cfg = provider_config_from_spec(llm, user_id=1, credential_ref=llm.credential_ref)
    self.assertEqual(cfg.credential_ref, 'my-openai')
```

Ensure `loop.py` passes `llm.credential_ref`.

- [ ] **Step 5: Run runner tests**

Run: `./olib/scripts/orunr py test backend/apps/runner/tests/ -v`

- [ ] **Step 6: Full gate**

Run: `./olib/scripts/orunr py test-all`

- [ ] **Step 7: Commit**

```bash
git add backend/apps/runner/
git commit -m "feat(runner): session loop uses tool instances and LLM credential_ref"
git fetch origin main && git rebase origin/main && git push
```

---

## S7 — Cleanup and docs

### Task 11: Remove `ToolPermission` references + delete stale test file

**Files:**
- Delete or merge: `backend/apps/agents/tests/test_tools_wiring.py` if superseded by `test_tool_wiring.py`
- Grep: `ToolPermission` in `backend/` — update any remaining imports

- [ ] **Step 1: Grep and fix**

Run: `rg ToolPermission backend/` — should return no Python imports (docs OK).

- [ ] **Step 2: Run full test suite**

Run: `./olib/scripts/orunr py test-all`

- [ ] **Step 3: Commit**

```bash
git add -A backend/
git commit -m "chore(agents): remove ToolPermission and finalize schema v1 call sites"
git fetch origin main && git rebase origin/main && git push
```

---

## Plan self-review (spec coverage)

| Design requirement | Task |
|--------------------|------|
| ToolInstance + credential_ref | S2 Task 4 |
| LLMSpec.credential_ref | S2 Task 4, S6 Task 10 |
| schema_version / spec_version 0→1 | S1–S3 |
| spec_migrations/migrations/001_tool_instances.py | S1 Task 2 |
| load_spec_dict on every load | S1 Task 3, S3 Task 5 |
| save as new row at latest version | S4 Task 6 |
| No bulk upgrade command | (omitted by design) |
| credential_type on Tool | S5 Task 7 |
| tool_wiring + bind hook | S5 Task 8 |
| Instance id wire names | S6 Task 9 |
| Runner bound tools + events | S6 Task 10 |
| AGENTS.local.md checklist | Already present from design iteration |
| Test migration chain | S1 Tasks 2–3, S3 |

---

## Execution handoff

Plan complete and saved to `docs/specs/2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-plan.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per stage/task, review between tasks (`superpowers/subagent-driven-development`).
2. **Inline Execution** — implement in this session with `superpowers/executing-plans`, batch checkpoints.

Which approach?
