# Trigger block conditions — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-08-30-trigger-block-conditions-design.md`](./2026-08-30-trigger-block-conditions-design.md)
**Plan:** [`2026-08-30-trigger-block-conditions-plan.md`](./2026-08-30-trigger-block-conditions-plan.md)
**Branch:** `feat/2026-08-30-trigger-block-conditions`
**Review range:** `15dcc23b03c47792ce933721dcd5980de0f5b31d..6afe83de0eb7f7c260907fc623487214b5fee4b0` (2026-08-30)

## Assessment

**Ready to merge?** Yes, after the fixes recorded below.

**Reasoning:** The schema, extensible fail-closed registry, Obsidian readiness, and all dispatch paths match the approved design. Review findings around interactive visibility, status-probe latency, accessibility assertions, and documentation were resolved before PR creation.

## Strengths

- `libs.agent_spec` adds strict backward-compatible `blocks` without a schema-version bump.
- `apps.agents.block_gate` keeps runner and web callers independent of Obsidian while failing closed without leaking provider details.
- Queue and schedule dispatch probe outside locks, preserve available queue items, and skip blocked cron ticks without catch-up.
- Obsidian readiness reuses the canonical `get_status` API and requires a literal `ready: true`.

## Issues

### Critical

None.

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/templates/web/partials/chatbox.html`; `backend/templates/web/session_detail.html` | Manual-start controls did not expose their block reason before submission. | Added render-time manual gating for the start textarea and New session control; POST checks remain authoritative. |
| 2 | Fixed | `backend/libs/clients/obsidian/client.py` | Readiness inherited the 30-second file-operation timeout, which could stall web workers and queue beats. | Status requests now use a dedicated two-second timeout; file operations retain their existing timeout. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/tests/test_button_triggers.py`; `backend/apps/web/tests/test_start_session.py` | UI tests asserted broad substrings and omitted manual/session surfaces. | Tests now pin control-specific `aria-describedby` and reason ids across agent and session pages. |
| 2 | Fixed | `backend/templates/web/partials/chatbox.html` | Block reasons were duplicated through `title` and visible `aria-describedby` text. | Removed `title`; retained visible accessible reason text. |
| 3 | Fixed | `backend/apps/agents/tests/test_spec.py` | New schema test methods lacked required purpose docstrings. | Added concise intent docstrings. |
| 4 | Rejected | `backend/libs/clients/obsidian/protocol.py` | The plan named `vault_status` while implementation uses `get_status`. | `get_status` landed independently on `main` as the canonical API; the rebase deliberately reused it without a compatibility alias. |

## Recommendations

- Add request-scoped readiness memoization only if pages commonly contain several triggers sharing the same tool and measured latency warrants it.
- Keep manual starts outside the budget gate unless that separate behavior change is explicitly designed.

## Re-review after Important fixes

**Review range:** `409605f59c35c23c6a262d940c1635aab19af1b4..6544740c8f882cd541e122613456cc0902affe80`

No Critical or Important findings remained. The following Minor findings were resolved before PR creation:

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 5 | Fixed | `backend/libs/agent_spec/examples/journal-obsidian.yaml` | The motivating shipped example omitted its `tool_ready` queue gate. | Added the vault condition to `inbox-worker`. |
| 6 | Fixed | `backend/apps/agents/tests/test_config_validation.py` | New YAML validation tests lacked purpose docstrings. | Added concise intent docstrings. |
| 7 | Fixed | `backend/apps/web/tests/test_button_triggers.py` | Blocked button accessibility was asserted only on agent detail. | The test now verifies both agent and session pages. |
