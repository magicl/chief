# Agent scheduling — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)

**Design:** [`2026-07-05-agent-scheduling-design.md`](./2026-07-05-agent-scheduling-design.md)

**Plan:** [`2026-07-05-agent-scheduling-plan.md`](./2026-07-05-agent-scheduling-plan.md)

**Branch:** `feat/2026-07-05-agent-scheduling`

**Review range:** `a8e7b1a..af97ed1` (2026-07-05)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Critical beat scheduler wiring, delete cleanup, design doc alignment, and scheduler test are addressed. Remaining minor items are acceptable for v1.

## Strengths

- `apps.queues` → `apps.runner` load-time import boundary respected (`send_task` by name only).
- `_active_triggers` scopes to current config; schedule/queue slot fills use `select_for_update`.
- Per-trigger `schedule_beat.py` sync with migration backfill; good tests in `test_schedule_beat.py` and `test_scheduling.py`.
- Queue bootstrap format, scoped `dispatch_queue_triggers_for_queue`, immediate `put_item` hook match spec.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/chief/celery.py` | `CELERY_BEAT_SCHEDULER` never applied to Celery app | `app.conf.beat_scheduler` set from settings |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 2 | Fixed | `backend/apps/agents/signals.py` | No `post_delete` cleanup for PeriodicTasks | `disable_schedule_trigger_beat_on_delete` + test |
| 3 | Fixed | `-design.md` | Design described 60s scan; implementation uses django-celery-beat | Design doc updated |
| 4 | Fixed | `backend/chief/tests/test_celery_config.py` | No test for DatabaseScheduler wiring | Added `TestCeleryBeatScheduler` |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 5 | Rejected | `materialize.py`, `signals.py` | Redundant beat sync on config save | Harmless; defer dedup |
| 6 | Rejected | `0006_sync_schedule_beat_tasks.py` | RunPython imports live sync code | Common Django pattern |
| 7 | Rejected | `scheduling.py` | `last_fired_at` no longer used for same-minute dedup | Intentional with per-trigger crontab |
| 8 | Rejected | `0005_trigger_scheduling_fields.py` | Bundled unrelated `config_source` AlterField | Harmless autogen drift |

## Recommendations

- ~~Wire `app.conf.beat_scheduler` in `chief/celery.py` before merge.~~ Done.
- ~~Add `post_delete` cleanup for schedule beat tasks.~~ Done.
- ~~Update `-design.md` for django-celery-beat architecture.~~ Done.
