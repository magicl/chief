# Session chat hidden inside collapsed execution rows

**Date:** 2026-08-02
**Branch:** `fix/activity-tree-child-visibility`

## Problem

Follow-up to `2026-08-02-activity-tree-alpine-init.md`. With the row template
initializing correctly, the session page renders the INPUT message and the
collapsed LLM lines, but the assistant's replies never appear: the session looks
like it produced no answer.

The runtime parents generated `output` messages and requested `tool` activities
to the `llm` turn that produced them, so those rows are same-session children of
the LLM activity. The row template rendered the same-session child `<ol>` inside
the `x-show="store.isExpanded(activityId)"` disclosure container, and LLM, tool,
and span rows start collapsed by design. Every message below the first LLM turn
was therefore hidden until the user manually expanded each execution row.

This contradicts the spec, which requires input/output messages to stay visible
and puts only curated details plus the Raw JSON disclosure behind expansion
(`docs/specs/2026-07-26-nested-activity-ui/…-design.md`, "Default expansion and
user intent"; `…-plan.md` Task 6).

Reproduced on a local compose stack: a Clock assistant session stored
`input → llm → (output, tool) → llm → output`, and the page showed only the
INPUT row and two collapsed LLM lines until the LLM rows were expanded by hand.

## Approach

Move the same-session child list out of the disclosure container so collapsing
an execution row hides that row's details, not the tree beneath it. Expansion
keeps owning curated details, Raw JSON, and the separately authorized sub-agent
child-session tree.

## Changes

- `backend/templates/web/session_detail.html`: the same-session child `<ol>` is
  now a sibling of the `detailId` disclosure instead of a descendant.
- `backend/apps/web/static/web/activity_tree.render.test.js`: the fixture now
  includes an `output` message parented to a nested `llm` turn (the real runtime
  shape), plus visibility assertions that distinguish a rendered row from one
  buried in a collapsed ancestor.

## Verification

- Command: `./olib/scripts/orunr js test-unit` against the pre-fix template (red check)
- Result: the two new visibility tests fail (`expected false to be true`); the
  collapsed-details guard passes.
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed — olib 12, web unit 65, web browser (chromium) 6.
- Command: `./olib/scripts/orunr js lint`
- Result: passed (exit 0).
- Command: `./olib/scripts/orunr js tsc`
- Result: passed (exit 0).
- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, migrate/collectstatic, bandit, tests).
- Manual: local compose stack, Clock assistant session — assistant replies and
  tool rows now render without expanding anything.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: <url when opened>
