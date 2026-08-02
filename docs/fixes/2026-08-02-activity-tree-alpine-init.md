# Activity tree rows render as inert template markup

**Date:** 2026-08-02
**Branch:** `fix/activity-tree-alpine-init`

## Problem

The session page renders no activity rows. Each `<li x-data="activityNode(...)">`
contains a copy of `#activity-node-template`, but every directive inside it stays
inert: `x-if` blocks are never expanded, `x-show` never sets `display`, and
`x-text` never fills in text. The panel therefore shows an empty list even when
the store holds input/output/llm activities.

Cause: Alpine's `initTree()` wraps its tree walk in `deferHandlingDirectives`, so
directive handlers — including `x-data` — run only *after* the walk finishes. The
walk visits the still-empty `<li>`, finds no children, and completes; only then
does `activityNode.init()` append the cloned row markup. Nothing walks that new
subtree. Alpine's MutationObserver fallback cannot rescue it either, because
`x-for` inserts and initializes clones inside `mutateDom()`, which disconnects the
observer for the duration of the callback.

## Approach

Initialize the cloned subtree explicitly: append the template content, then call
`Alpine.initTree()` on each appended row element. `initTree` marks elements with
`_x_marker` and skips already-marked ones, so this stays safe if Alpine ever
walks the same nodes, and `x-for`/`x-if` teardown already runs `destroyTree()` on
the whole `<li>` subtree, so effects are still cleaned up.

## Changes

- `backend/apps/web/static/web/activity_tree.js`: `activityNode.init()` calls
  `Alpine.initTree()` on the appended row elements.
- `backend/apps/web/static/web/activity_tree.render.test.js`: new jsdom test that
  mounts the real recursive template with a real Alpine instance and asserts that
  message bodies, collapsed execution lines, same-session child rows, and a
  loaded subagent child-session subtree all render.
- `backend/apps/web/static/web/vitest.config.js`: include the new render test.
- `package.json` / `pnpm-lock.yaml` (static/web): add `alpinejs` as a
  devDependency so tests can render with the framework the page actually uses.

## Verification

- Command: `pnpm run test:unit` against the pre-fix `activity_tree.js` (red check)
- Result: all 4 tests in `activity_tree.render.test.js` fail — no row bodies, no
  collapsed lines, nested and child-session rows missing.
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed — olib 12, web unit 62, web browser (chromium) 6.
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.
- Command: `./olib/scripts/orunr py test-all --full`
- Result: passed (lint, mypy, migrate/collectstatic, bandit, tests).

## Review

Reviewer verdict: ready to merge; no Critical or Important findings.

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Rejected | `backend/templates/web/base.html:8` | Production loads `alpinejs@3.x.x` from unpkg while tests pin `^3.15.12`, so a future 3.x release could diverge from what CI proved | The `^3.15.12` range deliberately mirrors the floating `3.x.x` tag: the lockfile keeps CI deterministic and `orunr js upgrade` re-syncs it to whatever unpkg serves. Pinning the CDN tag is a separate asset-policy decision, not part of this fix |
| 2 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.render.test.js` | Render suite covered same-session nesting only, not `childLoadState` / `childStore` subagent rows | Added a running-subagent fixture with a stubbed child snapshot; the child subtree and its `Open session` link are now asserted |
| 3 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.js:1017` | `init()` docstring still described only the clone, so the required `initTree` call reads as optional | Docstring now points at the reason comment |
| 4 | Minor | Rejected | `backend/templates/web/session_detail.html:129` | Row expressions dereference `activity` without a null guard, which `applySnapshot` could expose while it refills the activity map | Pre-existing, not touched by this fix: `applySnapshot` refills synchronously before Alpine flushes effects, and `x-for` teardown runs `destroyTree`, which dequeues effects for removed rows. Optional hardening, out of scope |
| 5 | Minor | Rejected | `backend/apps/web/static/web/activity_tree.js:1019` | Missing `#activity-node-template` is skipped silently instead of warning | The project has no `console.*` calls anywhere in its JS, and the new render suite fails loudly if the template stops resolving |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: <url when opened>
