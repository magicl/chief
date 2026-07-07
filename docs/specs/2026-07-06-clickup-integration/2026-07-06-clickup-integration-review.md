# ClickUp library and tool — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-06-clickup-integration-design.md`](./2026-07-06-clickup-integration-design.md)
**Plan:** [`2026-07-06-clickup-integration-plan.md`](./2026-07-06-clickup-integration-plan.md)
**Branch:** `feat/2026-07-06-service-integrations`
**Review range:** `a0dd9a0..1637e62` (2026-07-07)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All Important and Minor review findings have been addressed: `team_id` validation, 429/5xx retry, client test coverage, non-mutating `update_task`, and task pagination.

## Strengths

- `httpx` client with `MockTransport` test seam; lazy token per request
- Source adapter envelope matches Gmail shape (`service`/`resource_type`/`resource_id`)
- `ClickUpTool` failure mapping matches Gmail's `{ok, error: {kind, message}}`
- Example YAML denies `delete_task`; wiring test confirms `config.team_id` threading

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None identified | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `libs/tools/tools/clickup.py::_dispatch` (`list_spaces`) | Missing `team_id` calls `list_spaces('')` instead of clear `ValueError` per design error-handling table | Tool and client validate `team_id` |
| 2 | Fixed | `libs/clients/clickup/client.py::_request` | No retry on 429/5xx with `Retry-After` as design specified | Single retry with `Retry-After` |
| 3 | Fixed | `libs/clients/clickup/tests/test_client.py` | `update_task`, `create_comment`, `delete_task`, `list_spaces`, `list_lists` untested at client level | Client tests added |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `libs/tools/tools/clickup.py::_dispatch` (`update_task`) | `arguments.pop('task_id')` mutates caller dict in place | Copies fields without mutating caller dict |
| 2 | Fixed | `libs/clients/clickup/client.py::list_tasks` | No pagination beyond first page | `list_tasks_up_to` paginates |

## Recommendations

- Validate `team_id` before `list_spaces` dispatch (mirror Gmail's `subject` validation) — **done**
- Add single 429 retry with `Retry-After` in `_request` if inbox triage will poll frequently — **done**
