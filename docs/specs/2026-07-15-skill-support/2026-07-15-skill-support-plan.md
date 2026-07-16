# Skill support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. `/impl` creates or checks out the declared feature branch before the first code change. Then create `docs/specs/2026-07-15-skill-support/2026-07-15-skill-support-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Add skills (named prompt blocks) to agent configs, introduce a uniform `ToolContext` interface for all tools, and implement a `load_skill` auto-tool that lets agents discover and load skills on demand.

**Architecture:** New `ToolContext` dataclass in `libs/tools/` carries agent/session context to tools uniformly. `Tool` ABC gains `auto` flag, `should_include(ctx)`, and context-aware `functions(ctx, instance)` / `bind(ctx, instance)`. All existing tools migrate to the new interface. `LoadSkillTool` auto-includes when `skills[]` is non-empty. `tool_wiring.py` simplifies to one context-based loop.

**Tech Stack:** Python, pydantic, Django, existing libs/tools framework

**Branch:** `feat/2026-07-15-skill-support`

**ClickUp:** https://app.clickup.com/t/868kcw16x

| Stage | ClickUp action |
|-------|----------------|
| Already done at design start | Status `doing`, tag `agent`, Branch field set |
| Implementation complete + verification green + PR open | Status `review`; comment with PR URL |

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (or scoped tests while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-07-15-skill-support` from the plan, and after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`ai/commands/py-checks.md`)
- **Final task:** code review via **`superpowers/requesting-code-review`** (see mandatory **S_final** section below)
- Test naming: avoid words `error`, `exception`, `warning`, `deprecated` in test names (parproc highlighting)

## File structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/libs/tools/context.py` | `ToolContext` dataclass |
| Modify | `backend/libs/tools/base.py` | `Tool` ABC: add `auto`, `should_include`, update `functions`/`bind` signatures |
| Modify | `backend/libs/tools/tools/clock.py` | Adopt `ToolContext` signature |
| Modify | `backend/libs/tools/tools/gmail.py` | Adopt `ToolContext` signature in `bind`/`functions` |
| Modify | `backend/libs/tools/tools/clickup.py` | Adopt `ToolContext` signature in `bind`/`functions` |
| Modify | `backend/libs/tools/tools/queue.py` | Adopt `ToolContext` signature in `bind`/`functions` |
| Create | `backend/libs/tools/tools/load_skill.py` | `LoadSkillTool` auto-tool |
| Modify | `backend/libs/agent_spec/spec.py` | Add `SkillSpec`, `skills` field on `AgentConfigSpec` |
| Modify | `backend/apps/agents/tool_wiring.py` | Simplify to `ToolContext`-based loop with auto-tool pass |
| Modify | `backend/apps/agents/tools_wiring.py` | Register `LoadSkillTool` |
| Modify | `backend/apps/runner/tool_definitions.py` | Add `ctx` param, auto-tool pass |
| Modify | `backend/apps/runner/loop.py` | Build `ToolContext`, pass through |
| Modify | `backend/apps/agents/tests/test_tool_wiring.py` | Update tests for new signatures |
| Modify | `backend/apps/runner/tests/test_tool_definitions.py` | Update tests for new signatures, add auto-tool tests |
| Create | `backend/libs/tools/tests/test_load_skill.py` | LoadSkillTool unit tests |
| Create | `backend/libs/agent_spec/tests/test_skill_spec.py` | SkillSpec validation tests |
| Create | `backend/libs/agent_spec/examples/skills-demo.yaml` | Example spec with skills |

---

### Task 1: ToolContext and Tool ABC changes

**Files:**
- Create: `backend/libs/tools/context.py`
- Modify: `backend/libs/tools/base.py`
- Modify: `backend/libs/tools/__init__.py`

- [ ] **Step 1: Create `ToolContext` dataclass**

Create `backend/libs/tools/context.py`:

```python
"""Agent/session context passed to tools at wiring and definition time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from libs.agent_spec.spec import AgentConfigSpec


@dataclass(frozen=True)
class ToolContext:
    """Immutable snapshot of agent/session state available to every tool.

    Tools use this to observe the agent config (e.g. skills list), resolve
    credentials via ``secret_supplier_factory``, and access session identity
    without ad-hoc kwargs.
    """

    spec: AgentConfigSpec
    user_id: int | None = None
    agent_id: UUID | None = None
    session_id: UUID | None = None
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]] | None = None
    client_factories: dict[str, Callable[..., Any]] = field(default_factory=dict)
```

- [ ] **Step 2: Update `Tool` ABC in `base.py`**

Update `Tool` in `backend/libs/tools/base.py`:

```python
from libs.tools.context import ToolContext

class Tool(ABC):
    """A tool namespace (e.g. ``clock``) with one or more sub-functions."""

    name: str
    credential_type: str | None = None
    auto: bool = False  # True = self-selects via should_include, not listed in tools[]

    @abstractmethod
    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """Return LLM-visible function definitions, optionally using context for dynamic content."""
        raise NotImplementedError

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any] | None:
        """Return a bound invoke callable, or None to use default dispatch."""
        return None

    def should_include(self, ctx: ToolContext) -> bool:
        """For auto tools: whether this tool should be presented to the LLM."""
        return True

    def invoke(self, function: str, arguments: dict[str, Any]) -> Any:
        """Default function dispatch. Used when bind() returns None."""
        from libs.tools.context import ToolContext as _TC
        dummy_ctx = _TC.__new__(_TC)
        for fn in self.functions(dummy_ctx):
            if fn.name == function:
                return fn.handler(**arguments)
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')
```

Note: the `invoke` default is a fallback for simple tools where `functions()` ignores ctx (like clock). It constructs a minimal dummy context. Tools that need real context must override `bind()`.

Also add the `ToolInstance` import needed by the type hint — use a forward ref or conditional import since `libs/tools` should stay Django-free and `ToolInstance` is in `libs/agent_spec`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance
```

- [ ] **Step 3: Update `libs/tools/__init__.py` exports**

Add `ToolContext` to the public imports:

```python
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools, get_tool, register_tool

__all__ = ['ToolContext', 'all_tools', 'get_tool', 'register_tool']
```

- [ ] **Step 4: Run scoped tests**

```bash
./olib/scripts/orunr py test backend/libs/tools/ -v
```

Expected: existing tests may break (they use the old `functions()` signature). That's expected — we fix them in Task 2.

- [ ] **Step 5: Commit**

```bash
git add backend/libs/tools/context.py backend/libs/tools/base.py backend/libs/tools/__init__.py
git commit -m "feat: add ToolContext dataclass and update Tool ABC with auto/context support"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: Migrate existing tools to ToolContext

**Files:**
- Modify: `backend/libs/tools/tools/clock.py`
- Modify: `backend/libs/tools/tools/gmail.py`
- Modify: `backend/libs/tools/tools/clickup.py`
- Modify: `backend/libs/tools/tools/queue.py`

- [ ] **Step 1: Update ClockTool**

In `backend/libs/tools/tools/clock.py`, update `functions` signature:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance


class ClockTool(Tool):
    name = 'clock'

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """Return the clock tool's LLM-visible sub-functions."""
        return [
            ToolFunction(
                name='now',
                description='Return the current UTC time as an ISO-8601 string.',
                parameters={'type': 'object', 'properties': {}, 'required': []},
                handler=self._now,
                readonly=True,
            ),
        ]

    # ... _now stays the same
```

- [ ] **Step 2: Update GmailTool**

In `backend/libs/tools/tools/gmail.py`, update `bind` and `functions`:

```python
from libs.tools.context import ToolContext

# Add TYPE_CHECKING import for ToolInstance

class GmailTool(Tool):
    name = 'gmail'
    credential_type = 'gmail'

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke closed over a per-mailbox GmailClient."""
        config = instance.config if instance else {}
        supplier: Callable[[], str | None] = lambda: None
        if ctx.secret_supplier_factory and instance and instance.credential_ref:
            supplier = ctx.secret_supplier_factory(instance.credential_ref, self.credential_type)
        elif ctx.secret_supplier_factory and self.credential_type:
            supplier = ctx.secret_supplier_factory(None, self.credential_type)
        factory: Callable[..., GmailClientProtocol] = ctx.client_factories.get(self.name, GmailClient)
        client = factory(token_supplier=supplier, config=config)

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            try:
                return self._dispatch(client, function, arguments)
            except GmailError as exc:
                return _failure(exc)

        return invoke

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """LLM-visible Gmail functions (handlers require ``bind``)."""
        # ... same body as today, just new signature
```

- [ ] **Step 3: Update ClickUpTool**

Same pattern as Gmail — update `bind` and `functions` signatures in `backend/libs/tools/tools/clickup.py`.

- [ ] **Step 4: Update QueueTool**

In `backend/libs/tools/tools/queue.py`, update `bind` and `functions`:

```python
def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any]:
    """Return an invoke callable closed over session and agent context."""
    agent_id = ctx.agent_id
    session_id = ctx.session_id

    def invoke(function: str, arguments: dict[str, Any]) -> Any:
        # ... same dispatch as today using agent_id and session_id
    return invoke

def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
    """LLM-visible queue tool definitions (handlers require ``bind``)."""
    # ... same body as today, just new signature
```

- [ ] **Step 5: Run tool tests**

```bash
./olib/scripts/orunr py test backend/libs/tools/ -v
```

Expected: PASS (tools now match new ABC signature)

- [ ] **Step 6: Commit**

```bash
git add backend/libs/tools/tools/
git commit -m "refactor: migrate clock, gmail, clickup, queue tools to ToolContext interface"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 3: Simplify tool_wiring.py and update tool_definitions.py

**Files:**
- Modify: `backend/apps/agents/tool_wiring.py`
- Modify: `backend/apps/runner/tool_definitions.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`
- Modify: `backend/apps/runner/tests/test_tool_definitions.py`

- [ ] **Step 1: Rewrite `tool_wiring.py`**

Replace the contents of `backend/apps/agents/tool_wiring.py` with:

```python
"""Bind tool registry instances to per-user credential suppliers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libs.agent_spec import ToolInstance
from libs.tools.base import Tool
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools, get_tool


@dataclass(frozen=True)
class BoundToolInstance:
    instance_id: str
    tool_type: str
    invoke: Callable[[str, dict[str, Any]], Any]


def build_bound_tools(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
) -> dict[str, BoundToolInstance]:
    """Map tool instance ids to invoke callables with context wired.

    Processes explicit tools from ``instances`` (config tools[]), then scans
    the registry for auto-tools whose ``should_include(ctx)`` returns True.
    """
    bound: dict[str, BoundToolInstance] = {}
    # Explicit tools (from tools[])
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            raise ValueError(f'Unknown tool type {inst.type!r}')
        invoke = tool.bind(ctx, inst) or tool.invoke
        bound[inst.id] = BoundToolInstance(
            instance_id=inst.id,
            tool_type=inst.type,
            invoke=invoke,
        )
    # Auto-tools (self-selecting platform tools)
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        invoke = tool.bind(ctx) or tool.invoke
        bound[tool.name] = BoundToolInstance(
            instance_id=tool.name,
            tool_type=tool.name,
            invoke=invoke,
        )
    return bound
```

- [ ] **Step 2: Update `tool_definitions.py`**

Replace `backend/apps/runner/tool_definitions.py`:

```python
"""Build provider tool definitions from agent config permissions."""

from __future__ import annotations

from collections.abc import Callable

from libs.agent_spec import ToolInstance
from libs.tools.base import wire_tool_name
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools, get_tool
from libs.tools.schema import ToolDefinition


def build_tool_definitions(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
    is_allowed: Callable[..., bool],
) -> list[ToolDefinition]:
    """Build LLM tool definitions for explicit and auto-tools.

    Explicit tools are gated by ``is_allowed``; auto-tools bypass permission
    checks (they are platform-managed and have no user-configurable allow/deny).
    """
    definitions: list[ToolDefinition] = []
    # Explicit tools (from tools[])
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            continue
        for fn in tool.functions(ctx, inst):
            if not is_allowed(inst.id, fn.name, instance=inst):
                continue
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(inst.id, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    # Auto-tools (no permission gating)
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        for fn in tool.functions(ctx):
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(tool.name, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    return definitions
```

- [ ] **Step 3: Update `test_tool_wiring.py`**

In `backend/apps/agents/tests/test_tool_wiring.py`:

- Update `_EchoCredTool` to use new `functions(self, ctx, instance=None)` and `bind(self, ctx, instance=None)` signatures.
- Update all `build_bound_tools(...)` calls to pass `ctx=ToolContext(...)` instead of keyword args like `user_id=1`, `agent_id=...`, `session_id=...`, `client_factories=...`.
- Patch target for `make_secret_supplier` stays the same path but is now accessed via `ctx.secret_supplier_factory`.
- For tests that pass `user_id=1`, build a `ToolContext` with `user_id=1` and a `secret_supplier_factory` that wraps `make_secret_supplier`.
- For `client_factories`, pass them on `ToolContext(client_factories={'gmail': ...})`.

Example updated test:

```python
from libs.tools.context import ToolContext
from libs.agent_spec import AgentConfigSpec, LLMSpec

def _make_ctx(**kwargs):
    """Helper to build a minimal ToolContext for tests."""
    spec = kwargs.pop('spec', AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='test',
    ))
    return ToolContext(spec=spec, **kwargs)

class TestBuildBoundTools(OTestCase):
    def test_clock_instance_invokes_without_credentials(self):
        instances = [ToolInstance(id='clock', type='clock', allow=['now'])]
        ctx = _make_ctx(user_id=1)
        bound = build_bound_tools(instances, ctx=ctx)
        self.assertIn('clock', bound)
        result = bound['clock'].invoke('now', {})
        self.assertIsInstance(result, str)
```

- [ ] **Step 4: Update `test_tool_definitions.py`**

In `backend/apps/runner/tests/test_tool_definitions.py`:

Update `build_tool_definitions` call to pass `ctx=_make_ctx()`:

```python
from libs.tools.context import ToolContext
from libs.agent_spec import AgentConfigSpec, LLMSpec

def _make_ctx(**kwargs):
    spec = kwargs.pop('spec', AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='test',
    ))
    return ToolContext(spec=spec, **kwargs)

class TestBuildToolDefinitions(OTestCase):
    def test_two_instances_same_type_get_distinct_wire_names(self):
        instances = [
            ToolInstance(id='clock-a', type='clock', allow=['now']),
            ToolInstance(id='clock-b', type='clock', allow=['now']),
        ]
        ctx = _make_ctx()
        defs = build_tool_definitions(instances, ctx=ctx, is_allowed=lambda *_a, **_k: True)
        names = {d.name for d in defs}
        self.assertEqual(names, {'clock-a__now', 'clock-b__now'})
```

- [ ] **Step 5: Run all tests**

```bash
./olib/scripts/orunr py test-all
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/agents/tool_wiring.py backend/apps/runner/tool_definitions.py \
  backend/apps/agents/tests/test_tool_wiring.py backend/apps/runner/tests/test_tool_definitions.py
git commit -m "refactor: simplify tool_wiring and tool_definitions to use ToolContext"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 4: SkillSpec and schema update

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Modify: `backend/libs/agent_spec/__init__.py` (if SkillSpec needs exporting)
- Create: `backend/libs/agent_spec/tests/test_skill_spec.py`

- [ ] **Step 1: Add `SkillSpec` to `spec.py`**

In `backend/libs/agent_spec/spec.py`, add the `SkillSpec` model and the `skills` field:

```python
class SkillSpec(BaseModel):
    """Named prompt block loadable on demand via the load_skill tool."""

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    description: str = Field(min_length=1)
    content: str = Field(min_length=1)
```

Add to `AgentConfigSpec`:

```python
class AgentConfigSpec(BaseModel):
    schema_version: Literal[3] = 3
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    integrations: list[IntegrationSpec] = []
    triggers: list[TriggerSpec] = []
    tools: list[ToolInstance] = []
    queues: list[QueueSpec] = []
    skills: list[SkillSpec] = []
```

Add to the `_unique_instance_ids` validator:

```python
skill_ids = [s.id for s in self.skills]
if len(skill_ids) != len(set(skill_ids)):
    raise ValueError('duplicate skill id')
```

- [ ] **Step 2: Export `SkillSpec` from `__init__.py`**

If `backend/libs/agent_spec/__init__.py` exports other models, add `SkillSpec` there.

- [ ] **Step 3: Write tests**

Create `backend/libs/agent_spec/tests/test_skill_spec.py`:

```python
from pydantic import ValidationError

from libs.agent_spec import AgentConfigSpec, LLMSpec, SkillSpec

from olib.py.django.test.cases import OTestCase


class TestSkillSpec(OTestCase):
    def _base_spec(self, **overrides):
        defaults = dict(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='test',
        )
        defaults.update(overrides)
        return AgentConfigSpec(**defaults)

    def test_skills_default_empty(self):
        spec = self._base_spec()
        self.assertEqual(spec.skills, [])

    def test_valid_skill(self):
        spec = self._base_spec(skills=[
            SkillSpec(id='triage', description='Email triage rules', content='Classify emails...'),
        ])
        self.assertEqual(len(spec.skills), 1)
        self.assertEqual(spec.skills[0].id, 'triage')

    def test_duplicate_skill_id_rejected(self):
        with self.assertRaises(ValidationError):
            self._base_spec(skills=[
                SkillSpec(id='triage', description='A', content='X'),
                SkillSpec(id='triage', description='B', content='Y'),
            ])

    def test_empty_description_rejected(self):
        with self.assertRaises(ValidationError):
            SkillSpec(id='x', description='', content='Y')

    def test_empty_content_rejected(self):
        with self.assertRaises(ValidationError):
            SkillSpec(id='x', description='Y', content='')

    def test_invalid_skill_id_rejected(self):
        with self.assertRaises(ValidationError):
            SkillSpec(id='Bad-Id', description='Y', content='Z')
```

- [ ] **Step 4: Run tests**

```bash
./olib/scripts/orunr py test backend/libs/agent_spec/tests/test_skill_spec.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/libs/agent_spec/spec.py backend/libs/agent_spec/__init__.py \
  backend/libs/agent_spec/tests/test_skill_spec.py
git commit -m "feat: add SkillSpec to AgentConfigSpec (backward-compatible, no version bump)"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 5: LoadSkillTool and registration

**Files:**
- Create: `backend/libs/tools/tools/load_skill.py`
- Modify: `backend/apps/agents/tools_wiring.py`
- Create: `backend/libs/tools/tests/test_load_skill.py`

- [ ] **Step 1: Implement `LoadSkillTool`**

Create `backend/libs/tools/tools/load_skill.py`:

```python
"""Load-skill auto-tool: lets agents discover and load named prompt blocks on demand."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance


class LoadSkillTool(Tool):
    """Auto-tool that activates when the agent has skills configured.

    Embeds skill names and descriptions in the tool definition so the LLM sees
    them without making a call. ``load`` returns full content for one skill.
    """

    name = 'load_skill'
    auto = True

    def should_include(self, ctx: ToolContext) -> bool:
        """Include only when the agent config declares at least one skill."""
        return len(ctx.spec.skills) > 0

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """Build a single ``load`` function with the skill catalog in its description."""
        skill_list = '\n'.join(f'- {s.id}: {s.description}' for s in ctx.spec.skills)
        return [
            ToolFunction(
                name='load',
                description=(
                    f'Load a skill by name to get detailed instructions. '
                    f'Available skills:\n{skill_list}\n\n'
                    'You SHOULD call this tool whenever the current task or context '
                    'relates to one of the listed skills. Load the skill BEFORE '
                    'acting on the topic it covers.'
                ),
                parameters={
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Skill id to load.'},
                    },
                    'required': ['name'],
                },
                handler=self._unbound,
            ),
        ]

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke that looks up skill content by id from the frozen spec."""
        skills_by_id = {s.id: s.content for s in ctx.spec.skills}

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'load':
                return {'error': f'Unknown function {function!r}'}
            name = arguments.get('name', '')
            content = skills_by_id.get(name)
            if content is None:
                available = ', '.join(skills_by_id.keys())
                return {'error': f'Unknown skill {name!r}. Available: {available}'}
            return {'skill': name, 'content': content}

        return invoke

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('load_skill requires bind')
```

- [ ] **Step 2: Register in `tools_wiring.py`**

In `backend/apps/agents/tools_wiring.py`, add:

```python
from libs.tools.tools.load_skill import LoadSkillTool

register_tool('load_skill', LoadSkillTool())
```

- [ ] **Step 3: Write tests**

Create `backend/libs/tools/tests/test_load_skill.py`:

```python
from libs.agent_spec import AgentConfigSpec, LLMSpec, SkillSpec
from libs.tools.context import ToolContext
from libs.tools.tools.load_skill import LoadSkillTool

from olib.py.django.test.cases import OTestCase


def _make_ctx(skills=None):
    """Build a ToolContext with optional skills."""
    spec = AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='test',
        skills=skills or [],
    )
    return ToolContext(spec=spec)


class TestLoadSkillTool(OTestCase):
    def setUp(self):
        self.tool = LoadSkillTool()

    def test_should_include_false_when_no_skills(self):
        ctx = _make_ctx()
        self.assertFalse(self.tool.should_include(ctx))

    def test_should_include_true_when_skills_present(self):
        ctx = _make_ctx(skills=[SkillSpec(id='a', description='desc', content='body')])
        self.assertTrue(self.tool.should_include(ctx))

    def test_functions_embed_skill_names_in_description(self):
        ctx = _make_ctx(skills=[
            SkillSpec(id='triage', description='Email triage', content='...'),
            SkillSpec(id='style', description='Writing style', content='...'),
        ])
        fns = self.tool.functions(ctx)
        self.assertEqual(len(fns), 1)
        self.assertIn('triage: Email triage', fns[0].description)
        self.assertIn('style: Writing style', fns[0].description)

    def test_bind_returns_content_for_valid_skill(self):
        ctx = _make_ctx(skills=[
            SkillSpec(id='triage', description='Email triage', content='Classify by urgency'),
        ])
        invoke = self.tool.bind(ctx)
        result = invoke('load', {'name': 'triage'})
        self.assertEqual(result, {'skill': 'triage', 'content': 'Classify by urgency'})

    def test_bind_returns_failure_for_unknown_skill(self):
        ctx = _make_ctx(skills=[
            SkillSpec(id='triage', description='d', content='c'),
        ])
        invoke = self.tool.bind(ctx)
        result = invoke('load', {'name': 'nonexistent'})
        self.assertIn('error', result)
        self.assertIn('nonexistent', result['error'])

    def test_bind_returns_failure_for_unknown_function(self):
        ctx = _make_ctx(skills=[SkillSpec(id='a', description='d', content='c')])
        invoke = self.tool.bind(ctx)
        result = invoke('bad_func', {})
        self.assertIn('error', result)

    def test_auto_flag_is_true(self):
        self.assertTrue(self.tool.auto)
```

- [ ] **Step 4: Run tests**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_load_skill.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/libs/tools/tools/load_skill.py backend/apps/agents/tools_wiring.py \
  backend/libs/tools/tests/test_load_skill.py
git commit -m "feat: add LoadSkillTool auto-tool for on-demand skill loading"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 6: Runner integration and example spec

**Files:**
- Modify: `backend/apps/runner/loop.py`
- Create: `backend/libs/agent_spec/examples/skills-demo.yaml`
- Modify: `backend/libs/agent_spec/tests/test_examples.py`

- [ ] **Step 1: Update `SessionRunner.__init__` in `loop.py`**

Replace the bound-tools construction in `SessionRunner.__init__`:

```python
from libs.tools.context import ToolContext

# In __init__:
def _make_supplier(cred_ref: str | None, cred_type: str) -> Callable[[], str | None]:
    """Wrap make_secret_supplier into the ToolContext factory signature."""
    return make_secret_supplier(self.backend.user_id, name=cred_ref, type=cred_type)

self.ctx = ToolContext(
    spec=self.config_spec,
    user_id=self.backend.user_id,
    agent_id=getattr(session, 'agent_id', None),
    session_id=backend.session_id,
    secret_supplier_factory=_make_supplier if self.backend.user_id is not None else None,
    client_factories=client_factories or {},
)
self.bound_tools = build_bound_tools(self.config_spec.tools, ctx=self.ctx)
```

Remove the old `user_id=`, `agent_id=`, `session_id=`, `client_factories=` kwargs from `build_bound_tools`.

Also update `build_tool_definitions` call in `run()`:

```python
tool_definitions = build_tool_definitions(
    self.config_spec.tools,
    ctx=self.ctx,
    is_allowed=self._is_allowed,
)
```

Remove the `from apps.keys.services.queries import make_secret_supplier` import and add:

```python
from apps.keys.services.queries import make_secret_supplier
from libs.tools.context import ToolContext
```

(The import stays but is now used inside `__init__` to build the factory.)

- [ ] **Step 2: Create example spec**

Create `backend/libs/agent_spec/examples/skills-demo.yaml`:

```yaml
# title: Skills demo
# description: Minimal agent with skills and the clock tool, demonstrating on-demand skill loading.
schema_version: 3
description: Skills demo agent
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: |
  You are a helpful assistant. Load relevant skills before acting on topics they cover.
skills:
  - id: greeting-style
    description: "How to greet users based on time of day"
    content: |
      Greeting rules:
      - Before 12:00 UTC: "Good morning"
      - 12:00-17:00 UTC: "Good afternoon"
      - After 17:00 UTC: "Good evening"
      Always include the user's name if known.
triggers:
  - name: manual
    kind: manual
tools:
  - id: clock
    type: clock
    allow:
      - now
```

- [ ] **Step 3: Add example test**

In `backend/libs/agent_spec/tests/test_examples.py`, add:

```python
def test_skills_demo_example_validates(self) -> None:
    spec = load_example('skills-demo')
    validate_spec_tools(spec)
    self.assertEqual(len(spec.skills), 1)
    self.assertEqual(spec.skills[0].id, 'greeting-style')
```

- [ ] **Step 4: Run full test suite**

```bash
./olib/scripts/orunr py test-all
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/runner/loop.py backend/libs/agent_spec/examples/skills-demo.yaml \
  backend/libs/agent_spec/tests/test_examples.py
git commit -m "feat: wire ToolContext through runner and add skills-demo example"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

## S_final — Code review (mandatory)

### Task 7: Code review

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

- `{DESCRIPTION}` — Skill support: ToolContext, auto-tools, LoadSkillTool, tool migration
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-15-skill-support/2026-07-15-skill-support-design.md` and `-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Read `superpowers/requesting-code-review` skill and **`review-file-template.md`**.

1. Write `docs/specs/2026-07-15-skill-support/2026-07-15-skill-support-review.md`.
2. One issue table per severity with columns: `#`, **Status**, **Location**, **Finding**, **Notes**.
3. Summarize the same content in chat.

Stop here unless the user asks to fix issues.

- [ ] **Step 5: Track feedback**

When the user requests fixes or rejects findings, update **Status** in `*-review.md`:

- **Fixed** — after implementing the fix
- **Rejected** — when the user declines; record rationale in **Notes**

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` (PR / merge options).
