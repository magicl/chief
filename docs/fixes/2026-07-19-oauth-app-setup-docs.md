# Document OAuth application setup

**Date:** 2026-07-19
**Branch:** `fix/oauth-app-setup-docs`

## Problem

Chief documents credential values but does not give operators a single practical
guide for creating the Google and Dropbox OAuth applications that issue them.

## Approach

Add a focused operator guide that first explains callback URLs, then documents the
provider-specific setup and how each result is configured in Chief. Link it from the
agent credential reference without changing application behavior.

## Changes

- `docs/docs/oauth-apps.md`: added callback, Google, and Dropbox OAuth application
  setup instructions.
- `docs/docs/agents.md`: linked the new guide from the credential reference.

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: passed (Python lint, mypy, Bandit, migrations, static collection, and tests).
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed (22 unit, 4 browser, and 3 repository tests).
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.
- Command: `git diff --check` and fetch each external URL linked by the new page.
- Result: passed; all four external links returned successful responses.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `docs/docs/oauth-apps.md` | Dropbox token command used undefined variables and exposed the app secret in process arguments. | Added prompts; curl reads the secret interactively. |
| 2 | Important | Fixed | `docs/fixes/2026-07-19-oauth-app-setup-docs.md` | Required verification had not yet been run or recorded. | Full Python and JavaScript gates passed and are recorded above. |
| 3 | Minor | Fixed | `docs/docs/oauth-apps.md` | Disk credential guidance did not show that `value` must be a YAML string. | Added a complete `value: \|` example. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/29
