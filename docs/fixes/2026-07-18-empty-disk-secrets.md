# Allow empty disk secrets

**Date:** 2026-07-18
**Branch:** `fix/allow-empty-disk-secrets`

## Problem

Local credential YAML files with intentionally empty `value` fields fail synchronization because disk ingestion applies the interactive credential validation rule that rejects empty secrets. The reported unregistered Celery task came from a checkout behind `origin/main`, where the task registration is already merged.

## Approach

Accept empty strings only at the disk-provider boundary while retaining type and size validation. Database/UI credential writes continue to reject empty secrets.

## Changes

- Disk parser: treats a present YAML `value:` with no scalar as an empty string while still rejecting a missing field or non-string values.
- Credential commands: permit empty values only for disk-sourced writes; interactive/database writes retain non-empty validation.
- Tests: cover successful synchronization and encrypted storage of an empty disk value.

## Verification

- RED: `./olib/scripts/orunr py test` — new regression test failed because the empty value produced a per-file `ValueError`.
- GREEN: `./olib/scripts/orunr py test` — 210 olib tests and 589 backend tests passed.
- Gate: `./olib/scripts/orunr py test-all` — lint, mypy, tests, and bandit passed.

## Review

The independent code review found no Critical, Important, or Minor issues.

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/14
