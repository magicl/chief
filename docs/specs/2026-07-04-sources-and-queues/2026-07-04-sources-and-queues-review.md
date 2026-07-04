# Sources and queues — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-04-sources-and-queues-design.md`](./2026-07-04-sources-and-queues-design.md)
**Plan:** [`2026-07-04-sources-and-queues-plan.md`](./2026-07-04-sources-and-queues-plan.md)
**Branch:** `feat/2026-07-04-sources-and-queues`
**Review range:** `8503ae8..29fc5d0` (2026-07-04; post-fix re-review pending)

## Assessment

**Ready to merge?** With fixes (addressed in follow-up commits)

**Reasoning:** Core design and test coverage are strong. S10 focus areas had real gaps; items 1–7, 9–10 were fixed per user request. Item 8 deferred by user choice.

## Strengths

- Materialization matches `ARCHITECTURE.md` (`persist_agent_config` → `materialize_agent_config` → `sync_from_spec`)
- Solid take/complete/fail/release lifecycle with full `QueueItemAttempt` history
- Source orphan handling within queues; broad test coverage across commands, sync, tasks, tool wiring
- Clean `libs/agent_spec` extraction; queue tool registered and wired correctly
- Admin, `poll_source` CLI, stale-release beat at 120s

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `apps/queues/services/commands.py:76-100` | `put_item` dedup race — check-then-create with no transaction/`IntegrityError` handling on `(source, external_id)` unique constraint | `@transaction.atomic` + catch `IntegrityError`; test `test_put_handles_integrity_error_race` |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 2 | Fixed | `apps/queues/tasks.py` | `poll_source` crashes on adapter errors; no operational error state on `Source` | try/except; `last_error` / `last_error_at` fields + migration; clears on success |
| 3 | Fixed | `apps/queues/services/commands.py:362-403` | `sync_from_spec` does not remove orphan queues when dropped from spec; empty `queues=[]` was no-op | `_remove_orphan_queues`; delete if no items else disable sources |
| 4 | Fixed | `apps/queues/services/commands.py:104-147` | `take_item` does not verify session belongs to `queue.agent` | Validates `AgentSession.agent_id == queue.agent_id` |
| 5 | Fixed | `apps/queues/services/commands.py:285-307` | `release_stale_items` single transaction — one bad row aborts whole batch | Per-item `transaction.atomic()`; skip and log on `QueueItemStateError` |
| 6 | Fixed | `apps/queues/tests/test_commands.py` | Missing concurrent/race dedup test coverage | `test_put_handles_integrity_error_race` (IntegrityError path) |
| 8 | Rejected | (not implemented) | Plan/design v1 management commands `create_queue`, `queue_stats` missing | User: fix all except (8) — defer to spec 4 / ops follow-up |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 7 | Fixed | `apps/queues/services/commands.py` | Unused `_TERMINAL_ITEM_STATUSES` constant | Removed |
| 9 | Fixed | `libs/agent_spec/spec.py` | No pydantic `ge=1` on queue timing/attempt fields | `Field(ge=1)` + hold-seconds ordering validator |
| 10 | Fixed | `libs/agent_spec/spec.py` | No duplicate queue/source id validation in spec | Extended `_unique_instance_ids` validator |

## Recommendations

- Re-run full `orunr dev test-all` before merge (passed after fixes)
- Optional: second S_final review pass after fix commits land on the branch
