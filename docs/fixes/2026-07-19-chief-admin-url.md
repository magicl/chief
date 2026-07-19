# Align Chief admin URL with Floors

**Date:** 2026-07-19
**Branch:** `fix/chief-admin-url`

## Problem

Chief exposes Django admin at `/admin/`, while Floors uses the less predictable `/loelabs-admin/` path.

## Approach

Move Chief's admin routes and all active login/logout links to `/loelabs-admin/`, matching Floors. Keep historical specification documents unchanged and do not add Floors-specific 2FA behavior.

## Changes

- `backend/chief/urls.py`: expose Django admin at `/loelabs-admin/` and remove the old `/admin/` route.
- Web views, templates, and tests: use the matching `/loelabs-admin/login/` and `/loelabs-admin/logout/` endpoints.
- Project guidance: document the new admin path.
- URL regression test: verify canonical redirect, anonymous admin login redirect, and removal of the old route.

## Verification

- RED: `./olib/scripts/orunr py test` — the new URL regression test received 404 for `/loelabs-admin`.
- GREEN: `./olib/scripts/orunr py test` — 920 tests passed.
- Gate: `./olib/scripts/orunr py test-all` — lint, mypy, tests, and bandit passed.
- JavaScript: `./olib/scripts/orunr js test-unit`, `./olib/scripts/orunr js lint`, and `./olib/scripts/orunr js tsc` passed after initializing the configured JS dependencies.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No Critical, Important, or Minor findings. The reviewer assessed the change as ready to merge.

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/21
