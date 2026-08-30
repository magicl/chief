# Config Helper Trigger Tool Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-30-helper-trigger-tool-ready/2026-08-30-helper-trigger-tool-ready-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Make the config editor's Add trigger helper automatically gate new queue and schedule triggers on every readiness-reporting tool already declared in the YAML.

**Architecture:** Keep the behavior entirely in `apps.agents.services.config_mutations`: inspect the current round-trip YAML document, resolve each tool type through the existing registry, and compare its concrete `readiness` method with `Tool.readiness`. Attach ordered `tool_ready` block mappings only for eligible tools and trigger kinds, then rely on the existing whole-document validation path.

**Tech Stack:** Python, Django test runner, ruamel.yaml round-trip mappings, Chief tool registry, Pydantic agent config validation.

**Branch:** `feat/2026-08-30-helper-trigger-tool-ready`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped tests while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-30-helper-trigger-tool-ready`, and after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method written or materially changed
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`olib/ai/commands/py-checks.md`)
- **CLI stdout:** capture with `self.captureStdout()` and assert; do not leave `click.echo` status lines uncaptured (`olib/ai/commands/py-checks.md`)
- **Final task:** code review via `superpowers/requesting-code-review`
- Test names avoid parproc-highlighted words listed in `AGENTS.md`.
- Keep `docs/docs/agents.md` aligned with helper behavior; no schema-version bump is needed because the trigger block schema already exists.

### Task 1: Auto-inject tool readiness blocks

**Files:**
- Modify: `backend/apps/agents/tests/test_config_mutations.py`
- Modify: `backend/apps/agents/services/config_mutations.py`
- Modify: `docs/docs/agents.md`

- [ ] **Step 1: Add failing helper-mutation coverage**

Add a reusable valid YAML fixture with two Obsidian instances, a clock instance, and an existing manual trigger. Add tests that parse the updated YAML with `load_yaml_document` and assert:

```python
READINESS_TOOLS_YAML = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Process work.
tools:
  - id: first-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: first
      roots: [Journal]
  - id: clock
    type: clock
    allow: [now]
  - id: second-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: second
      roots: [Notes]
triggers:
  - name: manual
    kind: manual
queues:
  - id: inbox
"""

def test_add_schedule_trigger_gates_on_readiness_tools_in_document_order(self) -> None:
    """Schedule helpers gate on each readiness-reporting tool in YAML order."""
    updated = apply_config_mutation(
        READINESS_TOOLS_YAML,
        {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
    )
    trigger = load_yaml_document(updated)['triggers'][-1]
    self.assertEqual(
        trigger['blocks'],
        [
            {'kind': 'tool_ready', 'tool': 'first-vault'},
            {'kind': 'tool_ready', 'tool': 'second-vault'},
        ],
    )

def test_add_queue_trigger_gates_on_readiness_tool(self) -> None:
    """Queue helpers gate on readiness-reporting tools already in the YAML."""
    updated = apply_config_mutation(
        READINESS_TOOLS_YAML,
        {'action': 'add_trigger', 'name': 'worker', 'kind': 'queue', 'queue': 'inbox'},
    )
    trigger = load_yaml_document(updated)['triggers'][-1]
    self.assertEqual(trigger['blocks'], [{'kind': 'tool_ready', 'tool': 'first-vault'}, {'kind': 'tool_ready', 'tool': 'second-vault'}])

def test_add_trigger_with_only_always_ready_tools_omits_blocks(self) -> None:
    """Always-ready tools do not add an empty or ineffective block list."""
    raw = READINESS_TOOLS_YAML.replace(
        """  - id: first-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: first
      roots: [Journal]
""",
        '',
    ).replace(
        """  - id: second-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: second
      roots: [Notes]
""",
        '',
    )
    updated = apply_config_mutation(
        raw,
        {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
    )
    self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])

def test_manual_and_button_triggers_omit_readiness_blocks(self) -> None:
    """Interactive helper triggers remain ungated even when readiness tools exist."""
    for mutation in (
        {'action': 'add_trigger', 'name': 'manual-two', 'kind': 'manual'},
        {
            'action': 'add_trigger',
            'name': 'button',
            'kind': 'button',
            'button_text': 'Run',
            'prompt': 'Run now.',
        },
    ):
        with self.subTest(kind=mutation['kind']):
            updated = apply_config_mutation(READINESS_TOOLS_YAML, mutation)
            self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])

def test_add_schedule_trigger_with_valueless_tools_omits_blocks(self) -> None:
    """A valueless tools key behaves like an empty list during readiness scanning."""
    raw = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Process work.
tools:
triggers:
queues:
"""
    updated = apply_config_mutation(
        raw,
        {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
    )
    self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])
```

Also include a malformed tool row in a focused unit-level test of the readiness-block builder (or an otherwise validator-safe test seam) to prove entries missing `id` or `type`, plus unknown registry types, are skipped as the design requires.

- [ ] **Step 2: Run the scoped tests and verify RED**

Run:

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_config_mutations.py
```

Expected: FAIL because queue/schedule helper output has no `blocks`.

- [ ] **Step 3: Implement readiness-derived block insertion**

Import `Tool` from `libs.tools.base` and `get_tool` from `libs.tools.registry`. Add a documented helper that preserves source order and skips malformed, unknown, and base-readiness entries:

```python
def _tool_readiness_blocks(doc: CommentedMap) -> CommentedSeq:
    """Build ordered tool_ready blocks for tool types with custom readiness probes."""
    blocks: CommentedSeq = CommentedSeq()
    for item in _entries(doc, 'tools'):
        if not isinstance(item, dict):
            continue
        tool_id = item.get('id')
        tool_type = item.get('type')
        if not tool_id or not tool_type:
            continue
        tool = get_tool(tool_type)
        if tool is None or type(tool).readiness is Tool.readiness:
            continue
        block: CommentedMap = CommentedMap()
        block['kind'] = 'tool_ready'
        block['tool'] = tool_id
        blocks.append(block)
    return blocks
```

Change the `add_trigger` mutation so it computes and conditionally attaches blocks before append:

```python
if action == 'add_trigger':
    triggers = _entries_for_append(doc, 'triggers')
    trigger = _trigger_entry(mutation)
    if mutation['kind'] in {'queue', 'schedule'}:
        blocks = _tool_readiness_blocks(doc)
        if blocks:
            trigger['blocks'] = blocks
    triggers.append(trigger)
    return
```

- [ ] **Step 4: Run scoped tests and verify GREEN**

Run:

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_config_mutations.py
```

Expected: PASS, including existing comment-preservation and valueless collection coverage.

- [ ] **Step 5: Document the Add trigger helper default**

Under `docs/docs/agents.md` → `### Block conditions`, add:

```markdown
The config editor's **Add trigger** helper automatically inserts one `tool_ready`
condition per readiness-reporting tool already present in the document when it adds
a `queue` or `schedule` trigger. The helper preserves `tools[]` order. Manual and
button triggers remain ungated, tools added later do not backfill existing triggers,
and editing or removing conditions remains YAML-only.
```

- [ ] **Step 6: Run the full Python gate**

Run:

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0 for lint, mypy, tests, and bandit.

- [ ] **Step 7: Commit and sync the PR-ready chunk**

```bash
git add backend/apps/agents/services/config_mutations.py backend/apps/agents/tests/test_config_mutations.py docs/docs/agents.md
git commit -m "feat(config): gate helper triggers on tool readiness"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop and ask the human.

---

## S_final — Code review (mandatory)

### Task 2: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against this plan and the approved design. Write findings to `2026-08-30-helper-trigger-tool-ready-review.md` using `review-file-template.md`. Under `/ship`, return findings to the ship coordinator for automatic disposition.

**Files:**
- Create: `docs/specs/2026-08-30-helper-trigger-tool-ready/2026-08-30-helper-trigger-tool-ready-review.md`

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Dispatch the reviewer with the implementation summary; the design and plan paths; and the exact base/head SHAs.

- [ ] **Step 4: Write and disposition the review file**

Write one issue table per severity with columns `#`, `Status`, `Location`, `Finding`, and `Notes`. Under `/ship`, fix every actionable finding, mark each row `Fixed` or `Rejected` with rationale, re-run verification, and re-review after any Critical or Important fix.

- [ ] **Step 5: Return to ship for PR completion**

The ship coordinator performs squash, fresh post-squash verification, push, PR creation, and the `review` status transition.

---

## Out of scope

- New helper controls or client-side behavior.
- Backfilling blocks when tools are added after a trigger.
- Runtime trigger block evaluation or Obsidian readiness changes.
- Auto-gating manual, button, or agent triggers.
