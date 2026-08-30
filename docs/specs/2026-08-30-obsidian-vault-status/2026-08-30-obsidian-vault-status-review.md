# Obsidian vault `status` tool function — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-08-30-obsidian-vault-status-design.md`](./2026-08-30-obsidian-vault-status-design.md)
**Plan:** [`2026-08-30-obsidian-vault-status-plan.md`](./2026-08-30-obsidian-vault-status-plan.md)
**Branch:** `feat/2026-08-30-obsidian-vault-status`
**Review range:** `7217ee2a2aec77c19ffba68537bf96b0da8f4ecd..a5111932190a7550d421aa2c991ffb82067b5528` (2026-08-30)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Planned client, mock, no-stall tool dispatch, docs, and allow-list are in place. Review polish (docstrings and extra invalid-body/query assertions) is applied.

## Strengths

- Matches design/plan: `get_status`, mock without file-op gate, `status` not wrapped in `_call_with_retry`
- Strict bool parsing on the HTTP body; mock unseeded/not-ready paths do not stall
- Operator docs distinguish Chief first-sync/process liveness from `ob sync-status`

## Issues

### Critical

None.

### Important

None.

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/tools/tests/test_obsidian_tool.py:53-54` | Test docstring still says exactly list/read/write/append | |
| 2 | Fixed | `backend/libs/clients/obsidian/tests/test_client.py` | Query-less GET not asserted (`params` empty) | |
| 3 | Fixed | `backend/libs/clients/obsidian/tests/test_client.py` | Invalid-body coverage is only `ready: 1`; missing/empty `vault_id` untested | |
| 4 | Fixed | `backend/libs/tools/tools/obsidian.py` class docstring | Still “file operations” only | |

## Recommendations

- Keep file-op retry and status as separate dispatch paths.
- Polling `status` can still flip vault-service in-memory `ready` (existing GET behavior); out of scope.
