# Async-safe local disk bootstrap

**Date:** 2026-07-18
**Branch:** `fix/async-local-disk-bootstrap`

## Problem

Uvicorn imports Django's ASGI application while an asyncio event loop is active.
The web app's `AppConfig.ready()` hook synchronously accessed the ORM during local
disk boot sync, so Django raised `SynchronousOnlyOperation` and skipped the
initial import.

## Approach

Move boot synchronization out of the ASGI import thread while preserving the
existing retry, watcher, and management-command behavior.

## Changes

- `apps.web.local_bootstrap`: detect an active event loop and run the existing
  boot synchronization routine in a daemon thread; serialize provider writes
  across boot and watcher paths; close the short-lived thread's DB connection.
- `apps.web.tests.test_local_bootstrap`: cover Uvicorn-style startup and verify
  synchronization leaves the event-loop thread, concurrent boot calls coalesce,
  and watcher writes wait for boot synchronization.

## Verification

- Command: `./olib/scripts/orunr py test "$PWD/backend/apps/web/tests"`
- Result: 96 tests passed after the regression tests first failed on the original
  same-thread behavior.
- Command: `./olib/scripts/orunr py test-all`
- Result: full lint, mypy, test, and Bandit gate passed.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/apps/web/local_bootstrap.py` | Concurrent app-config boot calls could both synchronize an unsynced root. | The provider synchronization lock now covers the synced-root check through completion. |
| 2 | Important | Fixed | `backend/apps/web/local_bootstrap.py` | Watcher writes could overlap background boot synchronization. | Boot, path, and periodic synchronization share one process-local lock. |
| 3 | Minor | Fixed | `backend/apps/web/local_bootstrap.py` | The one-shot thread did not explicitly close its database connection. | Thread wrapper closes old connections before and after synchronization. |
| 4 | Minor | Fixed | `backend/apps/web/tests/test_local_bootstrap.py` | The async test did not join the worker or assert exactly one invocation. | Test tracks and joins the real spawned thread and asserts one off-thread call. |
| 5 | Minor | Fixed | `backend/apps/web/tests/test_local_bootstrap.py` | Timed joins did not assert worker termination, and watcher scheduling could permit a false positive. | Tests assert thread termination and use a tracking lock to rendezvous on the contested acquisition. |
| 6 | Minor | Fixed | `docs/fixes/2026-07-18-async-local-disk-bootstrap.md` | Focused verification retained the pre-review test count. | Updated from 94 to 96 tests. |

## Links

- PR: https://github.com/magicl/chief/pull/11
