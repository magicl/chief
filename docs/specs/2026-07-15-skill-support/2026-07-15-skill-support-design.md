# Skill support — Design

**Branch:** `feat/2026-07-15-skill-support`
Status: **done**

**ClickUp:** https://app.clickup.com/t/868kcw16x
**ClickUp branch field:** `feat/2026-07-15-skill-support`

Follow the `clickup` skill for status/tag/Branch updates.

---

## Goal

Chief agents can declare **skills** — named knowledge blocks with a description and
content — in their YAML config. A built-in `load_skill` tool lets the LLM discover
available skills and load their content on demand, so prompt text stays lean and the
LLM only pulls context it needs.

Alongside skills, this spec introduces a **`ToolContext`** dataclass and uniform tool
interface so tools can observe agent/session context, self-select for inclusion, and
generate dynamic tool definitions. The existing ad-hoc wiring in `tool_wiring.py`
(special cases for queue, credential tools, etc.) is replaced by a single context-based
contract.

### Deliverables

1. **`SkillSpec`** on `AgentConfigSpec` — `skills[]` list with `id`, `description`,
   `content` fields.
2. **`ToolContext`** — frozen dataclass carrying spec, identity, and injectable service
   callables (secret supplier factory, client factories).
3. **Uniform `Tool` interface** — `functions(ctx, instance)`,
   `bind(ctx, instance)`, `should_include(ctx)`, `auto` flag.
4. **`LoadSkillTool`** — auto-tool that activates when `skills[]` is non-empty; embeds
   skill names and descriptions in its tool definition; returns content on call.
5. **Migration of all existing tools** (clock, gmail, clickup, queue) to the new
   `ToolContext` interface.
6. **Simplified `tool_wiring.py`** — one loop for explicit tools, one for auto-tools;
   no per-tool-type branching.

### Non-goals

- External skill files (file refs, `$CHIEF_LOCAL_DIR/skills/`).
- Skill content from database or API.
- Spec version bump (this is a backward-compatible addition: `skills: []` default).
- UI for skill editing (future config UI spec).

---

## Current state

| Area | Today |
|------|-------|
| System prompt | Flat `system_prompt` string in `AgentConfigSpec` |
| Tool binding | Ad-hoc `bind()` signatures per tool type; `tool_wiring.py` branches on `tool.name` |
| Queue tool | Special-case in `bind_tool_invoke`: passes `user_id`/`agent_id`/`session_id` directly |
| Credential tools | `bind_tool_invoke` builds `token_supplier` and passes with `config`/`client_factory` |
| Tool inclusion | Only tools listed in config `tools[]` are presented to the LLM |
| `Tool.functions()` | No-arg method; static definitions |

---

## Schema: `SkillSpec`

```python
class SkillSpec(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    description: str   # short, shown to LLM in tool listing
    content: str       # full text, returned on load
```

Added to `AgentConfigSpec`:

```python
class AgentConfigSpec(BaseModel):
    schema_version: Literal[3] = 3
    # ... existing fields ...
    skills: list[SkillSpec] = []
```

### Validation

- Duplicate skill `id` → rejected (same as tool/queue id uniqueness).
- Empty `description` or `content` → pydantic validation.

### YAML example

```yaml
schema_version: 3
description: Email triage agent with skills
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  You triage email. Load relevant skills before acting.
skills:
  - id: email-triage
    description: "Guidelines for triaging email by priority and category"
    content: |
      When triaging email, classify each message into:
      - urgent: needs response within 1 hour
      - important: needs response today
      - fyi: read-only, archive after noting
  - id: writing-style
    description: "Tone and formatting rules for outbound replies"
    content: |
      Always use a professional but friendly tone.
      Keep responses under 200 words unless context requires more.
triggers:
  - name: manual
    kind: manual
tools:
  - id: clock
    type: clock
```

No spec version bump — `skills: []` is a backward-compatible optional field with a
default.

---

## ToolContext

```python
@dataclass(frozen=True)
class ToolContext:
    """Agent/session context passed to tools at wiring and definition time."""

    spec: AgentConfigSpec
    user_id: int
    agent_id: UUID | None = None
    session_id: UUID | None = None
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]]
    client_factories: dict[str, Callable[..., Any]]
```

Lives in `libs/tools/context.py` (Django-free). Every agent run has an owning
``user_id``. Credential tools resolve secrets via ``token_supplier_for(ctx,
credential_type=..., credential_ref=...)``, which keys off the tool's
``credential_type`` (ingest rejects ``credential_ref`` without one).

`secret_supplier_factory(credential_ref, credential_type)` wraps
`apps.keys.services.queries.make_secret_supplier` — tools call it to get a supplier
without importing Django apps.

---

## Uniform Tool interface

```python
class Tool(ABC):
    name: str
    credential_type: str | None = None
    auto: bool = False

    @abstractmethod
    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """Return LLM-visible function definitions, optionally using context."""
        ...

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any] | None:
        """Return a bound invoke callable, or None to use default dispatch."""
        return None

    def should_include(self, ctx: ToolContext) -> bool:
        """For auto tools: whether to include given the current agent context."""
        return True

    def invoke(self, function: str, arguments: dict[str, Any]) -> Any:
        """Default dispatch (unchanged from today)."""
        for fn in self.functions(ToolContext.__new__(ToolContext)):
            if fn.name == function:
                return fn.handler(**arguments)
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')
```

### Two tool categories

| Category | `auto` | In `tools[]` | Inclusion rule |
|----------|--------|-------------|----------------|
| **Explicit** | `False` | Yes | Always included if registered |
| **Auto** | `True` | Never | `should_include(ctx)` returns `True` |

Auto-tools use their `name` as the synthetic instance id (e.g. `load_skill`). Users
do not add them to `tools[]` and cannot configure allow/deny on them.

### `functions()` contract

- Explicit tools receive `instance` (the `ToolInstance` from config).
- Auto-tools receive `instance=None`.
- Tools that need dynamic descriptions (like `load_skill`) use `ctx` to generate them.
- Tools that don't need context ignore it (e.g. `clock`).

### `bind()` contract

- Returns a `(function, arguments) -> Any` callable, or `None` for default dispatch.
- Explicit tools receive `instance` to access `credential_ref`, `config`, etc.
- Auto-tools receive `instance=None` and use `ctx` directly.

---

## Existing tool migration

All four tools adopt `ToolContext`. Changes are mechanical:

### ClockTool

- `functions(self, ctx, instance=None)` — ignores ctx, returns same definitions.
- No `bind()` needed.

### GmailTool

- `functions(self, ctx, instance=None)` — ignores ctx, returns same definitions.
- `bind(self, ctx, instance=None)` replaces
  `bind(self, *, token_supplier, config, client_factory)`:
  - Builds supplier via `ctx.secret_supplier_factory(instance.credential_ref, self.credential_type)`.
  - Gets client factory from `ctx.client_factories`.
  - Same invoke closure internally.

### ClickUpTool

- Same pattern as Gmail.

### QueueTool

- `functions(self, ctx, instance=None)` — ignores ctx, returns same definitions.
- `bind(self, ctx, instance=None)` replaces
  `bind(self, *, user_id, agent_id, session_id)`:
  - Reads `ctx.agent_id`, `ctx.session_id` directly.
  - Same invoke closure internally.

---

## LoadSkillTool

```python
class LoadSkillTool(Tool):
    name = 'load_skill'
    auto = True

    def should_include(self, ctx: ToolContext) -> bool:
        return len(ctx.spec.skills) > 0

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        skill_list = "\n".join(f"- {s.id}: {s.description}" for s in ctx.spec.skills)
        return [ToolFunction(
            name='load',
            description=(
                f"Load a skill by name to get detailed instructions. "
                f"Available skills:\n{skill_list}\n\n"
                "You SHOULD call this tool whenever the current task or context "
                "relates to one of the listed skills. Load the skill BEFORE "
                "acting on the topic it covers."
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': 'Skill id to load.'},
                },
                'required': ['name'],
            },
            handler=self._unbound,
        )]

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable:
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

---

## Wiring changes

### `tool_wiring.py` — simplified

`bind_tool_invoke()` is removed. `build_bound_tools` becomes:

```python
def build_bound_tools(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
) -> dict[str, BoundToolInstance]:
    bound: dict[str, BoundToolInstance] = {}
    # Explicit tools (from tools[])
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            raise ValueError(f'Unknown tool type {inst.type!r}')
        invoke = tool.bind(ctx, inst) or tool.invoke
        bound[inst.id] = BoundToolInstance(
            instance_id=inst.id, tool_type=inst.type, invoke=invoke,
        )
    # Auto-tools
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        invoke = tool.bind(ctx) or tool.invoke
        bound[tool.name] = BoundToolInstance(
            instance_id=tool.name, tool_type=tool.name, invoke=invoke,
        )
    return bound
```

### `tool_definitions.py` — auto-tool pass

```python
def build_tool_definitions(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
    is_allowed: Callable[..., bool],
) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    # Explicit tools
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            continue
        for fn in tool.functions(ctx, inst):
            if not is_allowed(inst.id, fn.name, instance=inst):
                continue
            definitions.append(ToolDefinition(
                name=wire_tool_name(inst.id, fn.name),
                description=fn.description,
                parameters=fn.parameters,
            ))
    # Auto-tools (no permission gating)
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        for fn in tool.functions(ctx):
            definitions.append(ToolDefinition(
                name=wire_tool_name(tool.name, fn.name),
                description=fn.description,
                parameters=fn.parameters,
            ))
    return definitions
```

### `loop.py` — SessionRunner

Constructor builds `ToolContext` from existing data:

```python
self.ctx = ToolContext(
    spec=self.config_spec,
    user_id=self.backend.user_id,
    agent_id=getattr(session, 'agent_id', None),
    session_id=backend.session_id,
    secret_supplier_factory=make_secret_supplier,
    client_factories=client_factories,
)
self.bound_tools = build_bound_tools(self.config_spec.tools, ctx=self.ctx)
```

Tool definitions built with `ctx=self.ctx`. The `_is_allowed` method is unchanged —
auto-tools bypass it (they have no allow/deny since the user doesn't configure them).

### Registration

```python
# apps/agents/tools_wiring.py
from libs.tools.tools.load_skill import LoadSkillTool

register_tool('load_skill', LoadSkillTool())
```

---

## Testing

| Area | Tests |
|------|-------|
| `libs/agent_spec` | `SkillSpec` validation; duplicate skill id rejection; skills default `[]` |
| `libs/agent_spec` | Existing specs without `skills` still parse (backward compat) |
| `libs/tools` | `ToolContext` construction and field access |
| `libs/tools` | `LoadSkillTool.should_include` true/false based on skills |
| `libs/tools` | `LoadSkillTool.functions` embeds skill names in description |
| `libs/tools` | `LoadSkillTool.bind` returns content for valid skill, failure for unknown |
| `apps/agents/tool_wiring` | `build_bound_tools` includes auto-tools when applicable |
| `apps/agents/tool_wiring` | `build_bound_tools` excludes auto-tools when `should_include` is false |
| `apps/agents/tool_wiring` | Explicit tools still bind with `ToolContext` (clock, gmail regression) |
| `apps/runner/tool_definitions` | Auto-tool definitions appear when skills present |
| `apps/runner/tool_definitions` | Auto-tool definitions absent when no skills |
| `apps/runner/loop` | Session with skills can invoke `load_skill__load` |
| `apps/agents/tests` | Example specs with skills pass validation |

---

## Implementation stages

1. **ToolContext + Tool ABC** — `libs/tools/context.py`, update `Tool` in `base.py`.
2. **Migrate existing tools** — clock, gmail, clickup, queue to new signatures.
3. **Simplify tool_wiring.py** — remove `bind_tool_invoke`, use `ToolContext`.
4. **SkillSpec + schema** — add to `libs/agent_spec/spec.py`.
5. **LoadSkillTool** — implement and register.
6. **Runner + definitions** — update `tool_definitions.py` and `loop.py` for auto-tools and `ToolContext`.
7. **Tests** — full coverage per table above.
8. **Example spec** — add or update a YAML example with skills.

---

## Decisions (locked)

| Question | Decision |
|----------|----------|
| Skill storage | Inline in agent config YAML (`skills[]` on spec) |
| Skill discovery | `load_skill` tool with skill list in tool description |
| Skill loading | `load_skill__load` returns content on demand |
| Tool inclusion of `load_skill` | Auto-tool; activates when `skills[]` non-empty |
| Tool context interface | `ToolContext` dataclass with spec + identity + services |
| Tool self-selection | `auto=True` + `should_include(ctx)` for platform tools |
| Dynamic definitions | `functions(ctx, instance)` can use context |
| Queue tool migration | Same spec; migrates to `ToolContext` |
| Spec version bump | None — backward-compatible addition |

---

## References

- [Agent config schema design](../2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-design.md)
- [Architecture](../../ARCHITECTURE.md)
