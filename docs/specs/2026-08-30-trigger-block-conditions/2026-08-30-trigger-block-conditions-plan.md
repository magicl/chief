# Trigger block conditions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses `superpowers/subagent-driven-development` to implement this plan task-by-task in the prepared absolute worktree. Create `docs/specs/2026-08-30-trigger-block-conditions/2026-08-30-trigger-block-conditions-revision.md` before implementation for the human reviewer; do not read it during implementation. Steps use checkbox syntax. After all implementation tasks, return to `/ship` for mandatory S_final via `superpowers/requesting-code-review`.

**Goal:** Allow every trigger kind to declare ordered, fail-closed block conditions, beginning with an Obsidian-aware `tool_ready` condition that prevents sessions from starting until the configured tool is ready.

**Architecture:** Add a backward-compatible discriminated block schema in `libs.agent_spec`, then evaluate persisted trigger blocks through an `apps.agents` registry so runner and web callers share one gate without importing `apps.obsidian`. Tool readiness is a library-level extension: the default tool is ready, while Obsidian probes the existing vault service status endpoint through its shared client factory.

**Tech Stack:** Django, Pydantic, Celery dispatch services, Jinja, Obsidian HTTP/mock clients

**Branch:** `feat/2026-08-30-trigger-block-conditions`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …`; final gate is `./olib/scripts/orunr py test-all`
- **Git:** plan docs commit on `main`; implementation uses the declared feature branch. After every stage commit run `git fetch origin main && git rebase origin/main && git push`
- **TDD:** write each behavioral test first, run it to observe the expected failure, implement minimally, then rerun green
- **Function documentation:** per `AGENTS.md`, add a concise purpose docstring to every function/method written or materially changed and document non-obvious assumptions/order
- **No compatibility re-exports:** use canonical imports and delete replaced modules instead of leaving bridges
- **Test bases:** use `OTestCase`, `OTransactionTestCase`, or `OLiveServerTestCase`, never bare `unittest.TestCase`
- **Test names:** avoid parproc-highlighted terms such as `error`, `exception`, `warning`, and `deprecated`
- **Schema:** `blocks` is optional and backward-compatible; do not bump `AGENT_CONFIG_SPEC_VERSION` and do not add a migration
- **Agent docs:** update `docs/docs/agents.md` in the same change as the schema
- **Final task:** `/ship` owns S_final through `superpowers/requesting-code-review`

## File map

| File | Responsibility |
|------|----------------|
| `backend/libs/agent_spec/spec.py` | Block models, `TriggerSpec.blocks`, and tool-reference validation |
| `backend/apps/agents/block_gate.py` | Result types, kind registry, fail-closed ordered gate |
| `backend/apps/agents/block_wiring.py` | Built-in `tool_ready` evaluator registration |
| `backend/apps/agents/apps.py` | Register built-in block kinds at startup |
| `backend/libs/tools/base.py` | Default-ready tool API |
| `backend/libs/tools/tools/obsidian.py` | Obsidian vault readiness probe |
| `backend/libs/clients/obsidian/{protocol,client,mock}.py` | Vault status contract and implementations |
| `backend/apps/runner/scheduling.py` | Queue and schedule block gates |
| `backend/apps/runner/start.py` | Manual and button block gates |
| `backend/apps/web/services/queries.py` | Render-time button gate DTOs |
| `backend/templates/web/partials/chatbox.html` | Disabled blocked buttons and reason |
| `docs/docs/agents.md` | Operator schema and `tool_ready` documentation |

### Task 1: Block schema and operator documentation

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Test: `backend/apps/agents/tests/test_spec.py`
- Test: `backend/apps/agents/tests/test_config_validation.py`
- Modify: `docs/docs/agents.md`

- [ ] **Step 1: Write failing schema tests**

Add focused cases proving omitted and empty blocks are accepted, valid `tool_ready` references serialize unchanged, missing/unknown tool ids fail, unknown kinds fail, and known kinds reject extra fields:

```python
def test_trigger_blocks_default_empty(self) -> None:
    spec = AgentConfigSpec.model_validate(MINIMAL_SPEC_DICT)
    self.assertEqual(spec.triggers[0].blocks, [])

def test_tool_ready_block_references_declared_tool(self) -> None:
    raw = {
        **MINIMAL_SPEC_DICT,
        'tools': [{'id': 'vault', 'type': 'obsidian', 'config': {'vault': 'journal'}}],
        'triggers': [
            {
                'name': 'run',
                'kind': 'button',
                'button_text': 'Run',
                'prompt': 'Run now.',
                'blocks': [{'kind': 'tool_ready', 'tool': 'vault'}],
            }
        ],
    }
    spec = AgentConfigSpec.model_validate(raw)
    self.assertEqual(spec.triggers[0].model_dump(mode='json')['blocks'], raw['triggers'][0]['blocks'])
```

Use `self.assertRaises(ValidationError)` variants for absent tool references, `{'kind': 'future'}`, and extra `tool_ready` fields.

- [ ] **Step 2: Run tests and verify RED**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py backend/apps/agents/tests/test_config_validation.py -k block
```

Expected: failures because `TriggerSpec` does not accept or validate `blocks`.

- [ ] **Step 3: Implement the minimal schema**

In `spec.py`, define a strict block model and attach it to triggers:

```python
class ToolReadyBlockSpec(SpecModel):
    """Require one declared tool instance to report runtime readiness."""

    model_config = ConfigDict(extra='forbid')
    kind: Literal['tool_ready']
    tool: str


BlockSpec = Annotated[ToolReadyBlockSpec, Field(discriminator='kind')]


class TriggerSpec(SpecModel):
    blocks: list[BlockSpec] = Field(default_factory=list)
```

Add an `AgentConfigSpec` after-validator that compares each `tool_ready.tool` with `tools[].id` and raises a precise validation failure. Keep the current schema version unchanged. Confirm materialization needs no change because `_sync_triggers` already stores `model_dump(mode='json')`.

- [ ] **Step 4: Update operator docs**

Document `blocks`, ordered AND/short-circuit semantics, the strict `tool_ready.tool` reference, failure behavior, and the design’s queue/Obsidian YAML example in `docs/docs/agents.md`.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py backend/apps/agents/tests/test_config_validation.py -k block
```

Expected: pass with no schema-version change.

- [ ] **Step 6: Commit and synchronize**

```bash
git add backend/libs/agent_spec/spec.py backend/apps/agents/tests/test_spec.py \
  backend/apps/agents/tests/test_config_validation.py docs/docs/agents.md
git commit -m "feat(agent-spec): add trigger block conditions"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: Shared block-kind registry and gate

**Files:**
- Create: `backend/apps/agents/block_gate.py`
- Create: `backend/apps/agents/block_wiring.py`
- Modify: `backend/apps/agents/apps.py`
- Test: `backend/apps/agents/tests/test_block_gate.py`

- [ ] **Step 1: Write failing registry and gate tests**

Cover no blocks, ordered short-circuit AND, a test-registered non-tool kind, missing handler, evaluator failure, and operator-safe reasons:

```python
def test_blocks_short_circuit_in_declared_order(self) -> None:
    calls: list[str] = []

    def evaluate(_agent, _trigger, block):
        """Record evaluation and block on the first entry."""
        calls.append(block['name'])
        return BlockResult(ready=False, reason='dependency is pending')

    register_block_kind('test_ready', evaluate)
    result = blocks_allow_dispatch(self.agent, self.trigger_with_blocks([
        {'kind': 'test_ready', 'name': 'first'},
        {'kind': 'test_ready', 'name': 'second'},
    ]))
    self.assertFalse(result.ready)
    self.assertEqual(result.reason, 'dependency is pending')
    self.assertEqual(calls, ['first'])
```

Patch a handler to raise and assert the aggregate returns not-ready rather than propagating.

- [ ] **Step 2: Run tests and verify RED**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_block_gate.py
```

Expected: import failure because the gate does not exist.

- [ ] **Step 3: Implement result types, registry, and fail-closed gate**

Create frozen result dataclasses and a typed registry:

```python
@dataclass(frozen=True)
class BlockResult:
    """Represent one condition's readiness and operator-facing reason."""

    ready: bool
    reason: str = ''


@dataclass(frozen=True)
class BlockGateResult:
    """Represent the ordered aggregate result for one trigger."""

    ready: bool
    reason: str = ''
```

`register_block_kind(kind, evaluate)` validates slug names and installs handlers. `blocks_allow_dispatch(agent, trigger)` reads `trigger.spec.get('blocks', [])`, evaluates in order, returns on the first not-ready result, and logs trigger id, kinds, and safe reasons at info. Missing handlers and caught evaluator failures return a generic not-ready reason without leaking exception text.

`block_wiring.py` exposes an idempotent `wire_block_kinds()` and initially registers `tool_ready`; Task 3 fills in its evaluator. Call it from `AgentsConfig.ready()` next to tool wiring.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_block_gate.py
```

- [ ] **Step 5: Commit and synchronize**

```bash
git add backend/apps/agents/block_gate.py backend/apps/agents/block_wiring.py \
  backend/apps/agents/apps.py backend/apps/agents/tests/test_block_gate.py
git commit -m "feat(agents): add trigger block gate registry"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 3: Tool readiness and Obsidian vault status

**Files:**
- Modify: `backend/libs/tools/base.py`
- Modify: `backend/libs/tools/tools/obsidian.py`
- Modify: `backend/libs/clients/obsidian/protocol.py`
- Modify: `backend/libs/clients/obsidian/client.py`
- Modify: `backend/libs/clients/obsidian/mock.py`
- Modify: `backend/apps/agents/block_wiring.py`
- Test: `backend/libs/tools/tests/test_obsidian_tool.py`
- Test: `backend/libs/clients/obsidian/tests/test_client.py`
- Test: `backend/libs/clients/obsidian/tests/test_mock.py`
- Test: `backend/apps/agents/tests/test_block_gate.py`

- [ ] **Step 1: Write failing readiness tests**

Test the base tool’s ready default, mock status transitions, HTTP status decoding, Obsidian readiness through an injected client factory, unset service URL, and `tool_ready` resolution:

```python
def test_obsidian_readiness_reflects_vault_status(self) -> None:
    client = MockObsidianVaultClient()
    client.ensure_vaults([self.binding])
    client.set_ready('journal', False)
    ctx = ToolContext(spec=self.spec, user_id='1', agent_id='agent', client_factories={'obsidian': lambda: client})

    blocked = ObsidianTool().readiness(ctx, self.instance)
    self.assertFalse(blocked.ready)

    client.set_ready('journal', True)
    self.assertTrue(ObsidianTool().readiness(ctx, self.instance).ready)
```

The block-gate integration test must resolve the declared `tools[].id` and verify ordinary tools use the default-ready method.

- [ ] **Step 2: Run tests and verify RED**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py \
  backend/libs/clients/obsidian/tests/test_client.py \
  backend/libs/clients/obsidian/tests/test_mock.py \
  backend/apps/agents/tests/test_block_gate.py -k readiness
```

Expected: failures because readiness/status methods are absent.

- [ ] **Step 3: Extend the client contract**

Add `vault_status(*, vault_id: str) -> dict[str, Any]` to the protocol. The HTTP client sends `GET /v1/vaults/{vault_id}/status`; its existing request/error translation remains authoritative. The mock returns `vault_id`, `ready`, `initial_sync_complete`, and `sync_process_alive` from its vault record.

- [ ] **Step 4: Implement default and Obsidian readiness**

Add a documented default method on `Tool`:

```python
def readiness(self, ctx: ToolContext, instance: ToolInstance) -> BlockResult:
    """Report whether this tool can support a newly started session."""
    return BlockResult(ready=True)
```

Keep result types in a Django-free canonical module if needed to avoid importing `apps` from `libs`; `apps.agents.block_gate` should import/reuse that type. In `ObsidianTool.readiness`, reuse `_build_client`, parse the instance config, call `vault_status`, and return ready only for a truthy status. Unset `OBSIDIAN_VAULT_URL`, transport failures, 5xx, and non-ready status all fail closed with operator-safe reasons.

Implement `tool_ready` in `block_wiring.py`: load the agent’s current spec, resolve the exact tool id, construct `ToolContext` with user/agent ids and normal client factories, fetch the registered tool, then call `readiness`.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py \
  backend/libs/clients/obsidian/tests/test_client.py \
  backend/libs/clients/obsidian/tests/test_mock.py \
  backend/apps/agents/tests/test_block_gate.py
```

- [ ] **Step 6: Commit and synchronize**

```bash
git add backend/libs/tools backend/libs/clients/obsidian \
  backend/apps/agents/block_gate.py backend/apps/agents/block_wiring.py \
  backend/apps/agents/tests/test_block_gate.py
git commit -m "feat(obsidian): report vault readiness to trigger gates"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Gate queue and schedule dispatch

**Files:**
- Modify: `backend/apps/runner/scheduling.py`
- Test: `backend/apps/runner/tests/test_scheduling.py`
- Test: `backend/apps/runner/tests/test_limits_integration.py`

- [ ] **Step 1: Write failing dispatch tests**

Add queue cases proving a blocked gate creates no session and leaves the item `AVAILABLE`, then starts and takes it when ready. Add a schedule case proving blocked dispatch returns false, creates no session, and still sets `last_fired_at` to the attempted tick. Assert budget is evaluated before blocks.

```python
@patch('apps.runner.scheduling.blocks_allow_dispatch')
def test_blocked_queue_trigger_leaves_item_available(self, gate) -> None:
    gate.return_value = BlockGateResult(ready=False, reason='vault is syncing')
    dispatched = dispatch_queue_triggers_for_queue(self.queue.pk)
    self.item.refresh_from_db()
    self.assertEqual(dispatched, 0)
    self.assertEqual(self.item.status, QueueItemStatus.AVAILABLE)
    self.assertFalse(AgentSession.objects.exists())
```

- [ ] **Step 2: Run tests and verify RED**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_scheduling.py \
  backend/apps/runner/tests/test_limits_integration.py -k block
```

- [ ] **Step 3: Insert gates outside row locks**

In `_fill_queue_trigger_slots`, call `blocks_allow_dispatch` immediately after the successful budget check and before `transaction.atomic`; break without creating a session or taking an item when blocked.

In `dispatch_schedule_trigger`, evaluate blocks after budget and before the atomic row lock. On blocked result, update `last_fired_at=now` and return false, matching skipped budget behavior and preventing catch-up. Do not change the 15-second queue-trigger beat or add another schedule.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_scheduling.py \
  backend/apps/runner/tests/test_limits_integration.py
```

- [ ] **Step 5: Commit and synchronize**

```bash
git add backend/apps/runner/scheduling.py backend/apps/runner/tests/test_scheduling.py \
  backend/apps/runner/tests/test_limits_integration.py
git commit -m "feat(runner): gate automated trigger dispatch"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 5: Gate manual/button starts and render blocked buttons

**Files:**
- Modify: `backend/apps/runner/start.py`
- Modify: `backend/apps/web/services/queries.py`
- Modify: `backend/apps/web/views.py` if context shape requires it
- Modify: `backend/templates/web/partials/chatbox.html`
- Test: `backend/apps/runner/tests/test_start.py`
- Test: `backend/apps/web/tests/test_button_triggers.py`

- [ ] **Step 1: Write failing start and UI tests**

For manual and button starts, assert budget runs first, blocked results raise `StartSessionError` containing the operator reason, and no session exists. For rendering, assert blocked buttons remain visible but are disabled with the reason, while ready buttons remain enabled.

```python
@patch('apps.runner.start.blocks_allow_dispatch')
def test_button_block_reason_prevents_session_start(self, gate) -> None:
    gate.return_value = BlockGateResult(ready=False, reason='vault is syncing')
    with self.assertRaisesRegex(StartSessionError, 'vault is syncing'):
        start_button_session(self.agent, self.trigger)
    self.assertFalse(AgentSession.objects.exists())
```

- [ ] **Step 2: Run tests and verify RED**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_start.py \
  backend/apps/web/tests/test_button_triggers.py -k block
```

- [ ] **Step 3: Gate interactive starts**

In `start_button_session`, call blocks after the existing budget gate and before locking/capacity/session creation. In `start_manual_session`, add the same budget-then-block ordering before session creation. Convert a not-ready result to `StartSessionError` including its safe reason.

- [ ] **Step 4: Add render-time button state**

Have the web query/context layer return each trigger with `blocked` and `block_reason`, using `apps.agents.blocks_allow_dispatch`. Preserve button visibility. Render a blocked button as disabled with its reason in `title` and accessible adjacent text or `aria-describedby`; ready buttons retain the existing form action. The POST path remains authoritative and continues to catch `StartSessionError`.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_start.py \
  backend/apps/web/tests/test_button_triggers.py
```

- [ ] **Step 6: Commit and synchronize**

```bash
git add backend/apps/runner/start.py backend/apps/runner/tests/test_start.py \
  backend/apps/web/services/queries.py backend/apps/web/views.py \
  backend/templates/web/partials/chatbox.html backend/apps/web/tests/test_button_triggers.py
git commit -m "feat(web): expose trigger block reasons before session start"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 6: Cross-path verification and implementation artifact

**Files:**
- Create: `docs/specs/2026-08-30-trigger-block-conditions/2026-08-30-trigger-block-conditions-revision.md`
- Test/modify as required by failures: files from Tasks 1–5 only

- [ ] **Step 1: Create the untouched human revision template**

Create the standard revision artifact with empty review checkboxes. Do not add implementation findings and do not read it during implementation.

- [ ] **Step 2: Run focused acceptance paths**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py \
  backend/apps/agents/tests/test_block_gate.py \
  backend/libs/tools/tests/test_obsidian_tool.py \
  backend/apps/runner/tests/test_scheduling.py \
  backend/apps/runner/tests/test_start.py \
  backend/apps/web/tests/test_button_triggers.py
```

Expected: all block-schema, readiness, queue, schedule, manual, button, and UI cases pass.

- [ ] **Step 3: Run the complete Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0 for lint, mypy, tests, and security checks.

- [ ] **Step 4: Commit and synchronize remaining artifacts**

```bash
git add docs/specs/2026-08-30-trigger-block-conditions/2026-08-30-trigger-block-conditions-revision.md
git commit -m "docs: add trigger block conditions revision template"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 7: Full feature review

> **REQUIRED SKILL:** `/ship` reads and follows `superpowers/requesting-code-review`, dispatches the reviewer from `code-reviewer.md`, writes the review artifact, fixes every actionable finding, and re-verifies before finishing.

- [ ] **Step 1: Confirm the fresh full gate passes**

```bash
./olib/scripts/orunr py test-all
```

- [ ] **Step 2: Capture the review range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Dispatch the full-branch reviewer**

Review against this plan and `2026-08-30-trigger-block-conditions-design.md`, including schema compatibility, app import boundaries, fail-closed behavior, I/O outside locks, all dispatch kinds, UI accessibility, test quality, and required docstrings.

- [ ] **Step 4: Write and resolve the review**

Write `docs/specs/2026-08-30-trigger-block-conditions/2026-08-30-trigger-block-conditions-review.md` with Critical/Important/Minor tables. Under `/ship`, mark every actionable row `Fixed` or `Rejected` with technical rationale, rerun verification, and perform one more full review pass if any Critical/Important item was fixed.

- [ ] **Step 5: Return to `/ship` finishing**

Squash to one commit, rerun `./olib/scripts/orunr py test-all`, push with an exact force-with-lease only when workflow-owned history requires it, create the PR, and set design status to `review`. Do not merge or remove the worktree.

## Out of scope

- Named reusable condition aliases
- New readiness beat, webhook, or schedule catch-up
- Cancelling in-flight sessions
- Persisted blocked state or new SSE events
- Blocks on sources, integrations, or queues
- Agent config schema-version bump or migration
- Changes to Obsidian file-operation retry behavior

## Spec coverage

| Requirement | Task |
|-------------|------|
| Optional strict `blocks`, valid tool references, no version bump | 1 |
| Ordered AND, extensible registry, fail closed, info logging | 2 |
| Default tool readiness and Obsidian status probe | 3 |
| Queue leaves items available; schedule records skipped tick | 4 |
| Budget-before-block manual/button failures with reason | 5 |
| Disabled visible buttons with reason | 5 |
| Existing retry beats and no new beat/webhook | 4, 6 |
| Agent docs synchronized | 1 |
| Fresh full verification and code review | 6, S_final |
