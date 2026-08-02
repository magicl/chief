# Nested Activity UI — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md)
**Design:** [`2026-07-26-nested-activity-ui-design.md`](./2026-07-26-nested-activity-ui-design.md)
**Plan:** [`2026-07-26-nested-activity-ui-plan.md`](./2026-07-26-nested-activity-ui-plan.md)
**Branch:** `feat/2026-07-26-nested-activity-ui`
**Review range:** `8e3084732241120e408d73da830b2ef059ccbb67..8756202577351a629d8b4c9aeacd90428116b387` (2026-07-27)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Implementation matches the design; Important a11y `aria-controls` container and Minor cleanup items are Fixed. Re-review of `8756202` found no new Critical/Important issues.

## Strengths

- Strict child-session isolation in store + snapshot/SSE tests; no parent shortcut for child activities
- Cycle protection via ancestryPath and guarded ancestor walks
- Revision discipline with immutable canonical parent_id and onNeedsRefresh
- XSS-safe rendering through x-text/pre; Beautify gated to output only
- Resource lifecycle: one EventSource per expanded running session, bounded reconnect, dispose/BFCache refresh
- Auth ownership with indistinguishable 404 for foreign/missing children; root-only cost totals

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/templates/web/session_detail.html` (toggle `aria-controls` vs nested `store.childIds` list) | Expandable row `aria-controls` points at the curated-detail container, but same-session nested children render in a sibling `<ol>` also gated by `isExpanded`. Design requires the control to reference its detail/child container. | `detailId` now wraps curated details, subagent child tree, and same-session `childIds` `<ol>`; message kinds keep a separate always-visible children list. **Superseded** by `docs/fixes/2026-08-02-activity-tree-child-visibility.md`: gating the same-session list hid every assistant reply behind collapsed LLM rows, so that `<ol>` is a sibling of `detailId` again and the toggle controls only what it actually hides. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/templates/web/partials/agent_frame_styles.html` (`.activity-kind-text` / `.activity-status-*`) | Kind/status CSS utilities and status `::before` icons are unused because the collapsed line is one plain-text `formatCollapsedLine` span. | Removed unused kind/status CSS; status remains plain text in the collapsed line. |
| 2 | Fixed | `backend/apps/web/static/web/activity_tree.js` (`formatLatency` / `formatCollapsedLine`) | Duration uses only `latency_ms`; ignores `started_at`/`ended_at` when latency is absent. | `resolveLatencyMs` derives from parseable timestamps when `latency_ms` is absent; `latency_ms` still wins. |
| 3 | Fixed | `backend/apps/web/services/queries.py` / `backend/apps/web/views.py` | Parent breadcrumb walks the full ancestor chain (plus extra name/ownership queries) though only the direct parent is used; duplicated parent-payload composition with inconsistent name fallback. | Shared `get_owned_direct_parent`; name is stored value or null; template applies hex display fallback. |
| 4 | Fixed | `backend/templates/web/session_detail.html` (`activity-node-template`) | Template helpers like `formatUsd`/`renderOutput` resolve from enclosing `sessionView` scope without documenting that contract. | Brief HTML comment above the template documents the sessionView scope contract. |

## Recommendations

- None remaining; prior recommendations were addressed in `8756202`.
