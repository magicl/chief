# Obsidian vault service waits for a healthy backend

**Date:** 2026-08-29
**Branch:** `fix/obsidian-wait-for-backend`

## Problem

On Compose startup, `chief-obsidian` blocked in lifespan reconcile against
`http://chief-backend:8000` while `chief-backend` only waited for the Obsidian
container to *start*. That produced a burst of `httpx.ConnectError` tracebacks
(DNS, then connection refused) until Django was listening.

## Approach

Invert the gate without a healthcheck deadlock: Obsidian `depends_on`
`chief-backend` `service_healthy`. Backend no longer waits on Obsidian (Django
already treats a down vault as retryable). Worker/Beat stay gated on backend
only. Full `compose up` still starts Obsidian after backend is healthy;
`docker compose up chief-backend` alone no longer pulls the vault service.

## Changes

- `infra/docker/docker-compose.yml`: add `depends_on` on `chief-obsidian`; drop
  `chief-obsidian` from `chief-backend` `depends_on`.
- `backend/chief/tests/test_compose_config.py`: lock the one-way healthy gate.

## Verification

- Red: `manage.py test …test_vault_service_waits_for_healthy_backend_without_cycle` failed with `KeyError: 'depends_on'` on `chief-obsidian`.
- Green: same test OK.
- Command: `env -u VIRTUAL_ENV ./olib/scripts/orunr py test-all` (again after review follow-ups)
- Result: pass (lint, mypy, bandit, tests for `.`, `backend`, `infra`, `olib`, `services_obsidian`).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Fixed | `backend/chief/tests/test_compose_config.py` | Test claimed livez but only asserted `service_healthy`. | Assert backend `livez` probe and no Obsidian healthcheck. |
| 2 | Minor | Fixed | `docs/fixes/2026-08-29-obsidian-wait-for-backend.md` | Partial `compose up chief-backend` no longer starts Obsidian. | Documented as intended. |
| 3 | Minor | Rejected | `services/obsidian/obsidian_vault/reconcile.py` | Reconcile still uses `exc_info=True` on later failures. | Outside this compose-gate fix; revisit if spam remains after `compose up`. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/51
