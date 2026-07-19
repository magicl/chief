# Keys delete control

**Date:** 2026-07-19
**Branch:** `fix/keys-delete-control`

## Problem

The Keys page does not consistently expose deletion as the far-right action, and
its existing static-key control is a full text button rather than a compact red
cross.

## Approach

Render one delete form for every UI-owned key after any authentication controls.
Keep disk-owned declarations read-only and retain the existing POST endpoint.

## Changes

- Keys list: renders a compact red-cross delete action after any OAuth actions
  for every UI-owned credential.
- Keys page tests: verifies OAuth delete availability, ordering, styling, and
  accessible labeling.

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, tests, and bandit).
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.
- Command: `./olib/scripts/orunr js test-unit`
- Result: unit tests passed (22 tests); browser tests could not start because
  the host is missing the Chromium runtime library `libnspr4.so`.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

The reviewer found no Critical, Important, or Minor issues and assessed the
change as ready to merge.

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
