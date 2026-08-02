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
  message bodies, collapsed execution lines, and nested child rows render.
- `backend/apps/web/static/web/vitest.config.js`: include the new render test.
- `package.json` / `pnpm-lock.yaml` (static/web): add `alpinejs` as a
  devDependency so tests can render with the framework the page actually uses.

## Verification

- Command: `pnpm run test:unit` with the fix reverted (red check)
- Result: 3 failures in `activity_tree.render.test.js` — no row bodies, no
  collapsed lines, nested child row missing.
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed — olib 12, web unit 61, web browser (chromium) 6.
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.
- Command: `./olib/scripts/orunr py test-all --full`
- Result: passed (lint, mypy, migrate/collectstatic, bandit, tests).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: <url when opened>
