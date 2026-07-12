# Merge agent_specs into agent_spec

**Date:** 2026-07-12
**Branch:** `fix/868kavfmw-merge-agent-specs`

## Problem

`libs/agent_spec` (schema) and `libs/agent_specs` (examples + loaders) are nearly
identical names. Having both packages is confusing for imports and docs.

## Approach

Fold the examples library into `libs/agent_spec`: move `examples/` YAML, move
`list_examples` / `load_example` / `load_example_text` / `ExampleSpecInfo` into
`libs/agent_spec/examples.py`, re-export from `libs.agent_spec`, update call sites,
delete `libs/agent_specs`. Leave historical `docs/specs/**` paths unchanged.

## Changes

- `backend/libs/agent_spec/examples/` — shipped example YAML (moved)
- `backend/libs/agent_spec/examples.py` — list/load helpers (moved from agent_specs)
- `backend/libs/agent_spec/__init__.py` — re-export example helpers
- `backend/libs/agent_spec/tests/test_examples.py` — tests under agent_spec
- Call sites / `docs/ARCHITECTURE.md` — import and path updates
- Remove `backend/libs/agent_specs/`

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: pass (lint/mypy/bandit/backend tests green)

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `docs/fixes/…` | Verification section was pending | Filled after `py test-all` green |
| 2 | Minor | Fixed | `libs/agent_spec/tests/` | Missing `__init__.py` vs other libs | Added empty `__init__.py` |
| 3 | Minor | Fixed | `docs/ARCHITECTURE.md` table | Table did not mention shipped examples | Appended note on `libs/agent_spec` row |

## Links

- PR: https://github.com/magicl/chief/pull/1
- ClickUp: https://app.clickup.com/t/868kavfmw
