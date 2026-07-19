# Document per-type agent config fields

**Date:** 2026-07-19
**Branch:** `fix/agents-md-config-fields`

## Problem

`docs/docs/agents.md` describes tools, sources, and integrations at a high level, but
does not systematically list the type-specific `config` fields operators need when
authoring agent YAML. Schema changes can also drift from this doc because nothing
requires updating it.

## Approach

Add explicit per-type `config` field tables for tools and sources (and clarify that
integration `config` uses the same type-specific keys). Add an AGENTS.local.md rule
so schema/config changes always update `docs/docs/agents.md`.

## Changes

- `docs/docs/agents.md`: per-type config field tables for tools and sources
- `AGENTS.local.md`: require keeping `docs/docs/agents.md` in sync on schema/config changes

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: pass (lint, mypy, bandit, django migrate/collectstatic, py.test for backend/olib/infra)

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
