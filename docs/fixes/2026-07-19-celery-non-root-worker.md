# Run the Celery worker without root privileges

**Date:** 2026-07-19
**Branch:** `fix/celery-non-root-worker`

## Problem

The Compose worker ran as root and `backend/entrypoint.sh` set `C_FORCE_ROOT`,
bypassing Celery's protection against consuming pickle messages with superuser
privileges.

## Approach

Run the Compose worker as the host UID/GID, matching Floors' bind-mount pattern,
and remove the root-safety bypass from the shared entrypoint. This keeps
host-owned local config readable without granting superuser privileges. The
production image already runs the entrypoint as its non-root `app` user.

## Changes

- `infra/docker/docker-compose.yml`: run `chief-worker` as the host UID/GID.
- `backend/entrypoint.sh`: remove the conditional `C_FORCE_ROOT` bypass.
- `backend/chief/tests/test_compose_config.py`: cover the non-root Compose and
  entrypoint contract.

## Verification

- Red: `./olib/scripts/orunr py test` failed only because the worker had no
  configured `user`.
- Green: `./olib/scripts/orunr py test-all` passed lint, mypy, tests, and Bandit.
- Green: `./olib/scripts/orunr js test-unit` passed 29 tests across the configured
  JavaScript roots.
- Green: `./olib/scripts/orunr js lint` passed.
- Green: `./olib/scripts/orunr js tsc` passed.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `infra/docker/docker-compose.yml:119` | A fixed `nobody` user could lose access to owner-only bind-mounted local config. | Use Floors' host UID/GID pattern. |
| 2 | Minor | Fixed | `backend/chief/tests/test_compose_config.py:281` | The test hardcoded `nobody` rather than the bind-mount-compatible non-root identity. | Assert the host UID/GID Compose contract. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
