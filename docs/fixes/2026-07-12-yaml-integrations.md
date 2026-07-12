# YAML integrations block

**Date:** 2026-07-12
**Branch:** `fix/868k9u6zj-yaml-integrations`

## Problem

Agent YAML repeats the same connection details (`credential_ref`, mailbox
`subject`, ClickUp `team_id`, …) on both tool instances and queue sources.

## Approach

Add top-level `integrations[]` (schema v3). Tools and sources may set
`integration: <id>` to inherit `type`, `credential_ref`, and shared `config`;
instance fields still override. Legacy inline `type`/`credential_ref`/`config`
remain valid. Migration `2 → 3` only bumps version and defaults `integrations`
to `[]`.

## Changes

- `libs/agent_spec/spec.py` — `IntegrationSpec`; resolve refs on load
- `libs/agent_spec/migrations/003_integrations.py` — 2→3
- Example YAML (gmail/clickup) — use integrations
- Tests / SCHEMA_KEYS / ARCHITECTURE — document the shape

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: pass

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `yaml_dump.py` | Dump re-expanded integration fields | Collapse inherited fields on dump |
| 2 | Important | Fixed | `spec.py` `_apply_integration` | Null vs omitted credential_ref | Inherit only when key absent |
| 3 | Minor | Fixed | `queries.py` SCHEMA_KEYS | Missing `tools[].config` | Added |
| 4 | Minor | Fixed | tests | Gaps for dump collapse / null opt-out / dup ids | Added |

## Links

- PR: https://github.com/magicl/chief/pull/2
- ClickUp: https://app.clickup.com/t/868k9u6zj
