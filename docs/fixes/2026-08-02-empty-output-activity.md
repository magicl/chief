# Empty OUTPUT card on tool-call-only LLM turns

**Date:** 2026-08-02
**Branch:** `fix/empty-output-activity`

## Problem

When a model turn produces only tool calls and no assistant text, the session
page shows an empty green `OUTPUT` card immediately above the tool row, and the
tool row itself is bare text with no surface. The result reads as a rendering
failure.

Reproduced on the local compose stack. The stored activities for a Clock
assistant session are:

```
seq kind    name          summary                       details
1   input   input         What time is it?              {"content": "What time is it?"}
2   llm     gpt-5.4-mini  generate                      {}
3   output  output        (empty)                       {"content": ""}
4   tool    clock.now     clock.now completed           {"result": "2026-08-02T19:46:31…", …}
5   llm     gpt-5.4-mini  generate                      {}
6   output  output        The current UTC time is …     {"content": "The current UTC time is …"}
```

Row 3 is the bug: `SessionRunner._emit_output` recorded an `output` activity
unconditionally, including for the text-free turn that only requested
`clock.now`. The session page renders every `output` activity as a message card,
so an activity with no content becomes an empty card.

## Approach

Two parts, matching the two things visible on screen.

**Do not record text-free outputs of tool-bearing turns.** `_emit_output` skips
activity creation when the provider returned no non-whitespace content *and*
requested at least one tool. Provider reconstruction is unaffected in that case:
`rebuild_messages_from_activities` already synthesizes the
`{'role': 'assistant', 'content': '', 'tool_calls': [...]}` carrier message from
the `tool` activity when the preceding message is not an assistant message, so
dropping an empty `output` row rebuilds to the identical message list. A
regression test pins that equivalence.

The `tool_calls` condition is load-bearing. A text-free turn with no tool calls
has nothing downstream to replace its `output` row, so dropping it would leave
two consecutive `user` messages in the rebuilt history — which
`AnthropicProvider._prepare_messages` forwards to the API as-is rather than
merging. That turn keeps its empty `output` row, and a second test pins it.

Whitespace-only content is treated as text-free rather than preserved verbatim.
This is a deliberate normalization, not an identity-preserving transform: the
rebuilt assistant message carries `''` instead of the original blanks. No
provider distinguishes the two (`AnthropicProvider` only emits a text block
`if text:`), and blanks would still render as an empty card.

**Hide text-free messages that are already stored.** Sessions recorded before
this change still contain `output` rows with empty content, so the runner guard
alone would not repair the page the bug was reported from. The row template now
renders the message card only when the activity has a non-blank body
(`hasMessageBody`). The row itself still renders so its children stay reachable.

**Give execution rows a surface.** Collapsed `tool` / `llm` / `span` / `subagent`
rows previously rendered as one plain-text span on a transparent background. They
now carry the same kind pill vocabulary as the `INPUT` / `OUTPUT` messages plus a
subtle row background, so a tool row reads as a row rather than as loose text.
The `2026-07-26-nested-activity-ui` design's "do not introduce card-heavy
nesting" constraint still holds — execution rows stay one line tall and keep
their compact density.

## Changes

- `backend/apps/runner/loop.py`: `_emit_output` returns early when a tool-bearing
  turn produced content that is empty or whitespace-only.
- `backend/apps/web/static/web/activity_tree.js`: `formatCollapsedLine` is
  replaced by `formatKindLabel` (pill) plus `formatCollapsedDetail` (the rest of
  the line); the combined single-string form had no remaining caller, since
  `toggleLabel()` builds its own `aria-label`. Added `hasMessageBody`.
- `backend/templates/web/session_detail.html`: the collapsed execution row is a
  kind pill plus the detail text, the execution wrapper carries `kind-<kind>`
  for pill coloring, and message cards render only when `hasMessageBody`.
- `backend/templates/web/partials/agent_frame_styles.html`: pill colors for the
  execution kinds (`llm`, `tool`, `span`, `subagent`, `status`, `restart`),
  replacing the stale `kind-tool_call` / `kind-tool_result` rules for kinds the
  model no longer has; resting background/border on `.activity-toggle`.
- Tests: `backend/apps/runner/tests/test_loop.py`,
  `backend/apps/runner/tests/test_hooks.py`,
  `backend/apps/sessions/tests/test_rebuild.py`,
  `backend/apps/web/tests/test_session_dialog.py`,
  `backend/apps/web/static/web/activity_tree.test.js`,
  `backend/apps/web/static/web/activity_tree.render.test.js`.

### Incidental: pre-existing render-test flake

`activity_tree.render.test.js` → "hides only the child-session subtree when a
subagent row collapses" failed intermittently under `orunr js test-unit`, which
runs the olib, web-unit, and web-browser vitest projects side by side. Measured
on a worktree at `origin/main` (`ee8547d`), before any change from this fix:
**2 failures in 10 runs**; on this branch before the flake fix, 1 in 8. One
`Alpine.nextTick` plus a macrotask is not a reliable barrier for a store
mutation that reaches the DOM through an Alpine effect. The collapse assertion
now flushes until the row is hidden (`settleUntil`) and still fails if it never
hides. 12 consecutive green `orunr js test-unit` runs after the change.

## Verification

- Command: `python manage.py test apps.runner.tests.test_loop.TestSessionRunner.test_text_free_turn_records_no_output_activity` against the pre-fix runner (red check)
- Result: failed as expected — `AssertionError: Lists differ: ['   \n', 'done'] != ['done']`.
- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, bandit, migrate/collectstatic, 863 backend tests).
- Command: `./olib/scripts/orunr js test-unit`, 12 consecutive runs
- Result: all passed — olib 12, web unit 74, web browser (chromium) 6.
- Command: `./olib/scripts/orunr js lint`
- Result: passed (exit 0).
- Command: `./olib/scripts/orunr js tsc`
- Result: passed (exit 0).
- Manual: rendered the real `#activity-node-template` plus the real stylesheets
  against a fixture matching the session dumped above, before and after the
  change. Before: an empty `OUTPUT` card between the LLM row and a borderless
  `TOOL · clock.now · …` text line. After: no empty card, and the `LLM`, `TOOL`,
  and `SUBAGENT` rows each render as a pill-led row on a surface.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/apps/runner/loop.py:463` | The early return skipped the output row for *every* text-free turn, including turns with no tool calls. Nothing replaces that row, so rebuild produced two consecutive `user` messages, which `AnthropicProvider._prepare_messages` forwards unmerged. | Confirmed by reading `anthropic_provider.py:247-259` — user messages are appended verbatim. Skip is now gated on `result.tool_calls`, with `test_text_free_turn_without_tools_keeps_its_output_activity` pinning it. |
| 2 | Important | Fixed | `docs/fixes/2026-08-02-empty-output-activity.md` | The note claimed rebuild stays "byte-identical", which is untrue for whitespace-only content (rebuilds as `''`) and for the no-tool case. | Approach section now scopes the equivalence claim to tool-bearing turns with empty content and calls the whitespace handling a deliberate normalization. |
| 3 | Minor | Fixed | `backend/templates/web/session_detail.html:146` | Sessions recorded before this change still store empty `output` rows, so the runner guard alone would not repair the page the bug was reported from. | Added `hasMessageBody`; message cards render only for non-blank bodies, while the row still renders so children stay reachable. |
| 4 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.js:121` | `formatCollapsedLine` had no remaining production caller — `toggleLabel()` builds its own `aria-label` — so the "kept for aria-label" rationale was wrong. | Removed it; its unit tests now cover `formatKindLabel` + `formatCollapsedDetail`. |
| 5 | Minor | Fixed | `backend/apps/sessions/tests/test_rebuild.py:102` | The equivalence test covered `''` only, leaving the `strip()` policy unpinned. | The policy lives in the runner, so it is pinned by `test_text_free_turn_records_no_output_activity` (whitespace-only content plus a tool call) with a comment stating the intent; the note documents the normalization. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: <url when opened>
