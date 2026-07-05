# Agent scheduling — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)

**Design:** [`2026-07-05-agent-scheduling-design.md`](./2026-07-05-agent-scheduling-design.md)

**Plan:** [`2026-07-05-agent-scheduling-plan.md`](./2026-07-05-agent-scheduling-plan.md)

**Branch:** `feat/2026-07-05-trigger-prompt-and-session-end`

**Review range (follow-up):** `8532618..8d5597d` (2026-07-05)

## Follow-up (schema v2, trigger prompts, session termination)

**Branch:** `feat/2026-07-05-trigger-prompt-and-session-end`

**Review range:** `8532618..HEAD` (2026-07-05)

### Assessment

**Ready to merge?** Yes

**Reasoning:** Schema v2 migration inserts legacy default prompts on upgrade; `max_sessions` omission vs explicit null is distinguished; automated sessions terminate at waiting; runtime prompt fallback covers materialized v1 trigger rows until re-save.

### Changes since prior follow-up review

- **Schema v2** (`002_trigger_prompts`): v1 configs upgrade in memory with default schedule/queue/agent prompts matching former hard-coded bootstrap strings.
- **`max_sessions`**: omitted → default `1` for schedule/queue; explicit `null` → unlimited; manual always `null`.
- **Automated sessions** end at `waiting` → `done`; `active_session_count` excludes `waiting` for schedule/queue triggers.
- **Runtime** `trigger_prompt()` falls back to legacy defaults when materialized trigger JSON lacks `prompt`.

## Follow-up assessment (trigger prompt & session termination) — superseded

**Ready to merge?** No — with fixes

**Reasoning:** Waiting→done lifecycle and per-trigger prompts are implemented cleanly, but explicit `max_sessions: null` for unlimited concurrency is not achievable with the current validator (omitted and null both coerce to 1). Runtime dispatch for materialized triggers missing `prompt` can produce silent no-op sessions until configs are re-saved.

## Follow-up strengths

- `session_lifecycle.py` is well-scoped; manual sessions stay in `waiting`.
- `finalize_automated_trigger_session` in `run_session` `finally` covers success and failure paths.
- Prompt-driven bootstrap wired through spec, validation, mutations, scheduling, and tests.
- Queue releasability unchanged (`done` is releasable like `waiting`).

## Follow-up issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 9 | Fixed | `backend/libs/agent_spec/spec.py` | Explicit `max_sessions: null` conflated with omitted | `mode='before'` validator; omitted key → 1, explicit null → unlimited |
| 10 | Fixed | `backend/apps/runner/scheduling.py` | Empty bootstrap without prompt | `trigger_prompt()` legacy fallback |
| 11 | Fixed | schema v2 | No migration for missing prompts | `002_trigger_prompts.py` migration |
| 12 | Fixed | tests | Missing integration coverage | Added slot release, unlimited, queue lifecycle tests |
| 13 | Fixed | `test_scheduling.py` | Capacity tests used `WAITING` | Use `RUNNING`; post-completion dispatch test |
| 14 | Fixed | `spec.py` | Agent kind prompt | Required for all non-manual; migration adds default |
| 15 | Fixed | `scheduling.py` | Stale `WAITING` blocked capacity | Exclude `waiting` from schedule/queue active count |
| 16 | Fixed | docs | Design stale | Revision addendum for v2 |
| 17 | Fixed | `config_validation.py` | Duplicate prompt rules | Removed duplicate validation |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 10 | | `backend/apps/runner/scheduling.py:65-67` | Materialized triggers without `prompt` get empty bootstrap → no user INPUT → silent no-op session terminated as `done` | Runtime fallback, backfill, or documented breaking migration |
| 11 | | `backend/libs/agent_spec/spec.py`, `config_validation.py` | Save blocked for YAML without `prompt`; no migration path for configs from prior commit | Document breaking change or transition defaults |
| 12 | | tests | Missing: unlimited `max_sessions: null` dispatch; end-to-end slot frees after `run_session`; queue-kind lifecycle test | Add integration tests for stated requirements |
| 13 | | `backend/apps/runner/tests/test_scheduling.py` | Capacity tests use manually inserted `WAITING` rows; don't exercise post-change production flow | Add dispatch → run → second dispatch test |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 14 | | `backend/libs/agent_spec/spec.py:44-46` | `agent` kind requires `prompt` though agent dispatch is out of scope | Narrow to schedule/queue or document |
| 15 | | `backend/apps/runner/scheduling.py:41-46` | `WAITING` still in active count — stale pre-migration rows block capacity | Ops note or cleanup |
| 16 | | `docs/specs/2026-07-05-agent-scheduling/` | Design doc still describes locked bootstrap, idle `waiting`, min-1 `max_sessions` | Update design/revision for intentional deviations |
| 17 | | `config_validation.py:75-88` | Prompt rules duplicate Pydantic validator | Low drift risk |

## Follow-up recommendations

- Fix `max_sessions` null semantics (`Field(default=1)`); test omitted vs explicit null.
- Decide prompt backward compat: runtime fallback vs breaking migration checklist.
- Add end-to-end test: dispatch → `run_session` → `done` → second dispatch succeeds.
- Add queue termination test in `test_session_lifecycle.py`.
- Update design/revision docs for prompt, terminate-at-waiting, nullable unlimited.

---

## Original assessment (scheduling feature)

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
