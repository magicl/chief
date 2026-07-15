# Agent documentation in docs/docs/

**Date:** 2026-07-15
**Branch:** `feat/868kcw0w9-docs-folder`

## Problem

No user-facing documentation for the Chief agent format, tools, triggers,
queues, integrations, or credentials. Users had to read source code to
understand how to write agent YAML files.

## Approach

Create `docs/docs/agents.md` covering the full config spec surface
(envelope, AgentConfigSpec, triggers, tools with allow/deny, queues,
sources, integrations, credentials) and link it from `README.md`.
Reference shipped examples rather than duplicating config.

## Changes

- `docs/docs/agents.md`: new — comprehensive agent documentation
- `README.md`: added Documentation section with link to docs

## Verification

- Docs-only change; no code, no tests to run.
- Verified all relative links resolve to existing files.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/3
- ClickUp: https://app.clickup.com/t/868kcw0w9
