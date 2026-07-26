# Compact Integration Records — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Epic:** [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md)
**Design:** [`2026-07-26-compact-integration-records-design.md`](./2026-07-26-compact-integration-records-design.md)
**Plan:** [`2026-07-26-compact-integration-records-plan.md`](./2026-07-26-compact-integration-records-plan.md)
**Branch:** `feat/2026-07-26-compact-integration-records`
**Review range:** `0f105b0be34fc7e367b4d897f7ddf2d9a5ec235b..1e034c35e712f8855ce569aa8a0c716b10a47d0c` (2026-07-26)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All mandatory findings are fixed with recursive presentation-metadata coverage and the full Python gate passing.

## Strengths

- Clear Django-free projection boundary shared by tools and source adapters.
- Strong allowlist, malformed-input, bounds, and contract coverage.
- Compact acknowledgements and safe optional-comment failure handling.
- Fresh full suite passed with 1,157 tests.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/clients/clickup/projection.py:640,644` | Real ClickUp attachments use `mimetype` and may use numeric `date`; current projection loses required media type and creation time. | Provider-shaped attachment regression passes. |
| 2 | Fixed | `backend/libs/clients/clickup/projection.py:308,520` | Supported structured custom-field values are replaced with `None`, losing location, users, relationships, progress, and dropdown semantics. | Per-type allowlist regressions and full gate pass. |
| 3 | Fixed | `backend/libs/clients/clickup/projection.py:869` | `group_assignees` are mislabeled as mentions, while real tagged users in rich comment segments are omitted. | Rich-comment mention and contract regressions pass. |
| 4 | Fixed | `backend/libs/clients/clickup/projection.py:585` | Dropdown and label options expose provider presentation `color`, which the design explicitly excludes. | Focused option and recursive contract regressions pass. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|

## Recommendations

- Use sanitized provider-realistic fixtures for attachments, rich comments, and advanced custom fields.
