# Flaky activity-tree collapse assertion reddens CI

**Date:** 2026-08-02
**Branch:** `fix/activity-tree-collapse-flake`

## Problem

Nearly every `olib-dev-test-all` run since
`fix/activity-tree-child-visibility` merged is red, on `main` and on unrelated
PR branches. The failing job is always `js.test-unit::backend_apps_web_static_web`
with the same assertion:

```
FAIL activity_tree.render.test.js > recursive activity row rendering
     > hides only the child-session subtree when a subagent row collapses
AssertionError: expected true to be false
```

The test is flaky, not broken: locally it failed 3 of 6 unloaded runs and passed
the rest, and it always passes when its file runs alone.

Root cause (instrumented run under CPU load): after
`store.setExpanded('sub', false, { manual: true })` the store is already
collapsed — `isExpanded('sub') === false`, `manualCollapseIds === ['sub']`, a
single `#activity-detail-sub` in the DOM — but the Alpine `x-show` effect that
writes `display: none` has not run yet. Sampling `_x_isShown` on each macrotask
after the collapse shows it staying `true` for 2–4 extra ticks in failing runs:

```
FAIL shown/true shown/true none/false none/false …
FAIL shown/true shown/true shown/true shown/true none/false …
pass none/false none/false …
```

The suite's `settle()` waits one `Alpine.nextTick` plus one `setTimeout(0)`, a
fixed budget that a loaded CI runner outruns. The UI behaves correctly; the test
samples the DOM too early.

## Approach

Replace the fixed wait at the assertion boundary with a bounded poll: keep
settling until the DOM reflects the expected state or a deadline passes, then
assert. The helper does not throw on timeout, so the following `expect` still
produces the real failure message when a row genuinely fails to hide — a
regression stays red instead of turning into a helper stack trace.

The same fixed-budget assumption covers the asynchronously fetched child-session
subtree that `beforeAll` mounts, so that wait moves to the same helper.

No production code changes: the collapse behavior under test is correct.

## Changes

- `backend/apps/web/static/web/activity_tree.render.test.js`: added `waitUntil`,
  used it to wait for the child-session subtree to mount in `beforeAll` and for
  the collapse to reach the DOM before the visibility assertions.

## Verification

Flakes need a repetition count, not a single green run, so the suite was looped
against 12 busy-loop processes on a 32-core host to mimic a loaded CI runner.

- Command: `pnpm run test:unit` × 6, unloaded, before the fix
- Result: 3 failed / 3 passed — the flake, reproduced.
- Command: `pnpm run test:unit` × 15, under CPU load, after the fix
- Result: 15 passed, 0 failed.
- Command: `pnpm run test:unit` × 12, under CPU load, after the review fixes
- Result: 12 passed, 0 failed.
- Command: `pnpm run test:unit` with the child-session subtree moved out of the
  disclosure container in `session_detail.html` (temporary mutation, reverted)
- Result: failed after the 2s budget with the original
  `expected true to be false` — the bounded wait still catches a real
  regression instead of hiding it.
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed (exit 0) — olib 12, web unit 70, web browser 6.
- Command: `./olib/scripts/orunr js lint`
- Result: passed (exit 0).
- Command: `./olib/scripts/orunr js tsc`
- Result: passed (exit 0).
- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, bandit, migrate/collectstatic, tests).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.render.test.js:218` | A silent `waitUntil` timeout in `beforeAll` turns a child session that never mounts into a puzzling later failure instead of a mount failure | Assert the child-session row after the wait |
| 2 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.render.test.js:123` | The docstring promised the caller's assertion reports the mismatch, which only held for the collapse test | Reworded to require callers to assert the condition themselves |
| 3 | Minor | Fixed | `backend/apps/web/static/web/activity_tree.render.test.js:132` | Bare `2000` budget gave no hint of how it relates to the measured delay | Named `SETTLE_BUDGET_MS` with the measured two-to-four-macrotask bound |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
