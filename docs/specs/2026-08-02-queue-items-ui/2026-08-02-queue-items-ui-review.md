# Queue Items UI — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-08-02-queue-items-ui-design.md`](./2026-08-02-queue-items-ui-design.md)
**Plan:** [`2026-08-02-queue-items-ui-plan.md`](./2026-08-02-queue-items-ui-plan.md)
**Branch:** `feat/2026-08-02-queue-items-ui`
**Review range:** `f0eb9afb7c958fe9fae3b6aab900c646fc06728a..44e93abe2997077df3ba510542fccf5bedcc50cc` (2026-08-03)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Architecture, layering, and tests match the plan. Important findings (duplicate expand listeners; sync `user_id` lookup) fixed and re-reviewed clean; remaining items rejected as plan trade-offs or follow-ups.

## Strengths

- Clean layering: Django-free `libs/web_tables`, views call queue services only, no reverse imports
- Grouped status counts query; safe table-query fallbacks; secret-free scoped SSE envelopes
- Broad real-behavior tests; purposeful docstrings; accurate `ARCHITECTURE.md` deltas

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/templates/web/partials/queue_items_table.html` | Inline script re-registers a click listener on every htmx innerHTML swap of the table fragment, so Expand toggles cancel out after the first live refresh | Guard with `data-wired` |
| 2 | Fixed | `backend/apps/queues/services/commands.py` (`_resolve_agent_user_id` / `sync_from_spec`) | Re-queries `Agent.user_id` on every hint publish; `sync_from_spec` already has `agent` and should pass `agent.user_id` | Optional `user_id` on publish helpers; `sync_from_spec` passes `agent.user_id` |
| 3 | Rejected | `backend/apps/queues/services/commands.py` (`sync_from_spec`) | Publishes a `queues` hint for every agent materialize, including agents with no queues (plan trade-off; still costly system-wide) | Plan decision #7; deferred follow-up |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Rejected | `queue_items_table.html` | Full JSON payload rendered for every row (plan trade-off #11) | Acceptable for v1 per plan |
| 2 | Rejected | `queries.py` `q` filter | `Cast`+`icontains` with no index — fine at current scale | Follow-up if queues grow |
| 3 | Rejected | tests | No boundary test for `PAYLOAD_SEARCH_TEXT_CAP` | Nice to have; not blocking |

## Recommendations

- Use a `data-wired` guard idiom for page-local scripts that re-run on htmx swap
- Prefer `user_id: int` parameters on publish helpers where callers already hold the agent
