# Hierarchical Session Activities — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Epic:** [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md)
**Design:** [`2026-07-26-hierarchical-session-activities-design.md`](./2026-07-26-hierarchical-session-activities-design.md)
**Plan:** [`2026-07-26-hierarchical-session-activities-plan.md`](./2026-07-26-hierarchical-session-activities-plan.md)
**Branch:** `feat/2026-07-26-hierarchical-session-activities`
**Review range:** `cf232193973a18ba70e646466f177f5f96410fd6..d2429e0f8e285c50ce2f3f91b52c916faf64e8b1` (2026-07-26, final pass)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All findings from four review passes are fixed; the final full-range review found no remaining issues.

## Strengths

- Loss-preserving historical migration covers malformed and orphaned records.
- Sequence locking, revisioning, terminal immutability, and child-reference lock ordering are robust.
- SSE closes replay/subscription races and deduplicates by revision.
- Service boundaries and backend test coverage are strong.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/runner/backends/memory.py:88-126` | Memory provider reconstruction diverges from Django, including incorrect tool identity serialization and missing canonical lifecycle filtering/grouping. | Fixed in `6e6edc4`. |
| 2 | Fixed | `backend/apps/sessions/models.py:90-118` | Partial saves using FK attnames can bypass ancestry, agent, and config immutability checks. | Fixed in `6e6edc4`. |
| 3 | Fixed | `backend/apps/runner/tasks.py:58-83` | Cancellation can bypass the normal failure path and be finalized as `DONE`, incorrectly reconciling a parent reference as succeeded. | Fixed in `6e6edc4`. |
| 4 | Fixed | `backend/apps/runner/loop.py:256-262,405-420`; `backend/apps/runner/tasks.py:60-75` | Raw exception text and tracebacks can enter persisted activity details and SSE, violating the raw-safe details contract. | Fixed in `6e6edc4`. |
| 5 | Fixed | `backend/apps/sessions/migrations/0007_migrate_events_to_activities.py:308` | Legacy failure payloads are migrated unchanged, retaining historical raw diagnostics in activity details and SSE replay. | Fixed in `2f572f8`. |
| 6 | Fixed | `backend/apps/sessions/migrations/0007_migrate_events_to_activities.py:291-297` | Identifier-shaped historical values are accepted as safe codes without proving they belong to the known failure-code vocabulary. | Fixed in `d2429e0`. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/runner/backends/memory.py:173` | Empty-string update status is treated as unchanged instead of rejected like Django. | Fixed in `2f572f8`. |

## Recommendations

- Share canonical reconstruction semantics between memory and Django backends and add parity tests.
- Treat relation field names and attnames as immutable during partial saves.
- Preserve cancellation at the task boundary without marking the session done.
- Keep diagnostic exception text in logs only; persist stable failure codes/messages.
- Sanitize legacy failure payloads during migration.
- Reject empty or otherwise invalid memory-backend update statuses exactly like Django.
- Allowlist known historical failure codes and map every unknown value to `legacy_failure`.
