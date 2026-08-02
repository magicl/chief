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

**Do not record text-free outputs.** `_emit_output` now skips activity creation
when the provider returned no non-whitespace content. Provider reconstruction is
unaffected: `rebuild_messages_from_activities` already synthesizes the
`{'role': 'assistant', 'content': '', 'tool_calls': [...]}` carrier message from
the `tool` activity when the preceding message is not an assistant message, so
dropping the empty `output` row produces a byte-identical message list. A
regression test pins that equivalence.

**Give execution rows a surface.** Collapsed `tool` / `llm` / `span` / `subagent`
rows previously rendered as one plain-text span on a transparent background. They
now carry the same kind pill vocabulary as the `INPUT` / `OUTPUT` messages plus a
subtle row background, so a tool row reads as a row rather than as loose text.
The `2026-07-26-nested-activity-ui` design's "do not introduce card-heavy
nesting" constraint still holds — execution rows stay one line tall and keep
their compact density.

## Changes

- `backend/apps/runner/loop.py`: `_emit_output` returns early for content that is
  empty or whitespace-only.
- `backend/apps/web/static/web/activity_tree.js`: added `formatCollapsedDetail`,
  which is `formatCollapsedLine` without the leading kind segment (the kind now
  renders as a pill). `formatCollapsedLine` is kept for `aria-label`-style plain
  text and remains exported.
- `backend/templates/web/session_detail.html`: the collapsed execution row is a
  kind pill plus the detail text, and the execution wrapper carries `kind-<kind>`
  for pill coloring.
- `backend/templates/web/partials/agent_frame_styles.html`: pill colors for the
  execution kinds (`llm`, `tool`, `span`, `subagent`, `status`, `restart`),
  replacing the stale `kind-tool_call` / `kind-tool_result` rules for kinds the
  model no longer has; resting background/border on `.activity-toggle`.
- Tests: `backend/apps/runner/tests/test_loop.py`,
  `backend/apps/sessions/tests/test_rebuild.py`,
  `backend/apps/web/static/web/activity_tree.render.test.js`.

## Verification

- Command: `python manage.py test apps.runner.tests.test_loop.TestSessionRunner.test_text_free_turn_records_no_output_activity` against the pre-fix runner (red check)
- Result: failed as expected — `AssertionError: Lists differ: ['   \n', 'done'] != ['done']`.
- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, bandit, migrate/collectstatic, 863 backend tests).
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed — olib 12, web unit 73, web browser (chromium) 6.
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

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: <url when opened>
