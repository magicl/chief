# Gmail library and tool — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-06-gmail-integration-design.md`](./2026-07-06-gmail-integration-design.md)
**Plan:** [`2026-07-06-gmail-integration-plan.md`](./2026-07-06-gmail-integration-plan.md)
**Branch:** `feat/2026-07-06-service-integrations`
**Review range:** `a0dd9a0..1637e62` (2026-07-07)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All Important and Minor review findings have been addressed: HttpError mapping, richer source envelope, label creation, pagination, attachment decode/size guard, client tests, and operator setup notes.

## Strengths

- Clean `libs/clients/gmail` client with `service_factory` test seam and per-operation credential resolution
- Source adapter keeps triage filters in `config.query` only (no hardcoded `x-*` logic)
- `GmailTool` exposes full surface with correct `readonly` flags; example YAML denies `send`/`trash`
- `ToolInstance.config` + wiring landed with tests (shared with ClickUp)

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None identified | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `libs/clients/gmail/client.py`, `libs/tools/tools/gmail.py` | Real `googleapiclient.errors.HttpError` is never translated to `GmailError` subclasses; `GmailTool`'s `except GmailError` does not catch live API failures — uniform `{ok:false, error}` shape only holds in unit tests | `_execute` + `_map_http_failure` now wrap all API calls |
| 2 | Fixed | `libs/sources/adapters/gmail.py` | Missing `include_body` config; envelope `data` omits `to`, `has_attachments`, `attachments` from design example | Envelope extended; `include_body` validated |
| 3 | Fixed | `libs/clients/gmail/client.py`, `libs/tools/tools/gmail.py` | No `create_label`; `label` tool only wraps `modify_labels` | `create_label`, `ensure_label_ids`, `add_names` on label tool |
| 4 | Fixed | `libs/clients/gmail/tests/test_client.py` | `get_attachment`, `send_message`, `trash` lack direct client unit tests | Direct client tests added |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `libs/clients/gmail/client.py::list_messages` | No pagination loop — single API page only | `list_message_ids` paginates |
| 2 | Fixed | `libs/clients/gmail/client.py::get_attachment` | Returns raw base64 API response, not decoded bytes with size guard | Decodes base64; 10 MiB guard |
| 3 | Fixed | `docs/ARCHITECTURE.md` | No operator note on SA JSON + domain-wide delegation setup | Admin-console setup note added |

## Recommendations

- Add `_execute` helper mapping `HttpError` → typed `GmailError` before merge or track as spec 9 prerequisite — **done**
- Extend source envelope with attachment metadata when `has_attachments` is detectable from metadata format — **done**
