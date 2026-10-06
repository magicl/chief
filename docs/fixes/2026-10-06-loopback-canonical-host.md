# Compose loopback host redirect

**Date:** 2026-10-06
**Branch:** `fix/loopback-canonical-host`

## Problem

Opening the compose site as `127.0.0.1` (or `[::1]`) is a different site from `localhost`. Cookies and copied links then belong to a different host than `localhost`.

## Approach

**Tests:** code (failing test first)

The compose edge redirects those hosts to `localhost` with a 308, keeping the port from the Host header so a slot published as `8081` stays on `8081`. Service-to-service names are unchanged.

## Changes

- `infra/docker/nginx.conf`: map `$http_host` to `localhost` for numeric loopback, then 308
- `backend/chief/tests/test_compose_config.py`: the map and redirect must be present

## Verification

- Command: `./olib/scripts/orunr py test-all` (worktree)
- Result: pass (`py.test::backend` 16.92s, gate exit 0)

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Fixed | `backend/chief/tests/test_compose_config.py` | The test did not lock the empty default or the `if` that keeps other hosts from redirecting | Asserted both |

## Links

- PR: https://github.com/oivindloe/chief/pull/65
