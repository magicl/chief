# Refresh agent documentation

**Date:** 2026-07-19
**Branch:** `fix/agent-docs-refresh`

## Problem

The agent documentation described only the static credential-file shape and
contained several stale or omitted schema and runtime details.

## Approach

Align the reference documentation with the current credential parser, agent
schema, trigger runtime, OAuth providers, and automatic skill tool.

## Changes

- Document static and OAuth credential-file forms and Google authentication
  behavior.
- Document skills, session and trigger limits, owner resolution, and the
  reserved agent-trigger status.
- Add the automatic skill tool and shipped skills example to the reference.

## Verification

- Command: `git diff --check`
- Result: passed.
- Command: `./olib/scripts/orunr py test-all`
- Result: passed.
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed (22 unit, 4 browser, and 3 repository tests).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Two independent reviews found no remaining Critical, Important, or Minor
issues after the identified documentation inaccuracies were corrected.

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
