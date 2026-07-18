# Quiet routine Celery logs

**Date:** 2026-07-18
**Branch:** `fix/quieter-celery-logs`

## Problem

Chief's development worker and beat processes log routine scheduler dispatches, task receipts, and successful completions at INFO, making terminal output noisy.

## Approach

Raise only the Celery worker and beat process log levels to WARNING. This suppresses INFO records from those terminals while preserving warnings and errors; other Chief processes remain unchanged.

## Changes

- `backend/entrypoint.sh`: run both Celery worker and beat with `--loglevel=WARNING`.
- Compose configuration test: lock worker/beat thresholds and prevent INFO from returning.
- Project guidance: keep the documented worker command aligned with the entrypoint.

## Verification

- RED: `./olib/scripts/orunr py test` — the new entrypoint logging test failed while both commands still used INFO.
- GREEN: `./olib/scripts/orunr py test` — 210 olib tests and 589 backend tests passed.
- Gate: `./olib/scripts/orunr py test-all` — lint, mypy, tests, and bandit passed.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Rejected | `backend/entrypoint.sh:23` | WARNING also suppresses application INFO emitted inside Celery processes. | Intentional: the requested terminal policy is to hide INFO and retain failures for worker/beat processes; namespace filtering would be incomplete and fragile. |
| 2 | Important | Rejected | `backend/chief/tests/test_compose_config.py:61` | Static command assertions do not behaviorally test logger routing. | The configuration under test is the command threshold itself; Python logging tests would not exercise Celery CLI setup, while WARNING→ERROR retention is standard level behavior. |
| 3 | Minor | Fixed | `AGENTS.local.md:49` | Documented worker command still used INFO. | Updated to WARNING. |
| 4 | Minor | Fixed | `backend/chief/tests/test_compose_config.py:5` | Module description covered only local-provider configuration. | Broadened to entrypoint conventions. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/15
