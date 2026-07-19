# Show Google OAuth scope names

**Date:** 2026-07-19
**Branch:** `fix/show-google-oauth-scopes`

## Problem

The Keys page describes each Google OAuth capability but does not show the exact
OAuth scope URL that Chief requests.

## Approach

Append each capability's provider-defined scope URL in parentheses to its
description. Keep the catalog, selection behavior, and authorization flow
unchanged.

## Changes

- OAuth UI catalog: include each provider-defined scope URL in the secret-free
  capability metadata passed to the template.
- Keys page: append the exact scope URL in parentheses to each capability
  description.
- Regression coverage: require every catalog description and scope URL to render
  together.

## Verification

- Baseline: `./olib/scripts/orunr py test-all`
- Result: passed before changes.
- Red: `./olib/scripts/orunr py test`
- Result: the new assertion failed because the description omitted the scope URL.
- Green: `./olib/scripts/orunr py test`
- Result: passed all 946 backend tests after exposing and rendering the scope.
- Full gate: `./olib/scripts/orunr py test-all`
- Result: passed lint, mypy, all tests, and Bandit.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No findings. The reviewer assessed the change as ready to merge.

## Links

- PR: https://github.com/magicl/chief/pull/24
