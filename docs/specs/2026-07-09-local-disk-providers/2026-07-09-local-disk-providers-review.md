# Local disk providers — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-09-local-disk-providers-design.md`](./2026-07-09-local-disk-providers-design.md)
**Plan:** [`2026-07-09-local-disk-providers-plan.md`](./2026-07-09-local-disk-providers-plan.md)
**Branch:** `feat/2026-07-09-local-disk-providers`
**Review range:** `190d356..4c67114` (2026-07-10)

## Assessment

**Ready to merge?** With fixes

**Reasoning:** Architecture, conflict/soft-disable/read-only rules, secret hygiene, and tests look solid. Do not merge until Critical #1 (DB work in `AppConfig.ready()` can break `migrate`) is fixed; Important #2 (schedule beat not re-enabled on unchanged re-add) should be fixed or explicitly deferred.

## Strengths

- Clean `apps.local_disk` separation; consumers read DB only
- Secret hygiene: logs avoid YAML exception text that could echo secrets; tests assert secrets stay out of logs
- Conflict rules enforced in service layer (`upsert_user_named_from_disk`, agent persist)
- Content-hash change detection with CRLF normalization; envelope stripped before validate
- Soft-disable wired through resolve, dispatch, manual start, and beat disable
- Strong parse/sync/watch/UI test coverage; migrations generated via Django

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/apps.py`, `apps/web/local_bootstrap.py` | `WebConfig.ready()` runs `sync_all()` (ORM writes) before migrations; with `CHIEF_LOCAL_DIR` set this can abort `migrate` / boot when new columns are missing | Skips ORM-unsafe commands, defers via `post_migrate`, and contains boot sync failures |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 2 | Fixed | `backend/apps/local_disk/agent_sync.py` | Delete → re-add **unchanged** agent file sets `status=active` but skips `persist_agent_config` when `source_rev` unchanged → schedule beat stays disabled | Call `sync_agent_schedule_triggers` on disabled→active; add regression test |
| 3 | Fixed | `backend/apps/web/local_bootstrap.py` | Long-lived watcher thread never calls `close_old_connections()`; stale DB connections can fail forever after reconnect | Calls `close_old_connections()` at the top of each poll loop |
| 4 | Fixed | `backend/apps/web/local_bootstrap.py` | Multi-worker web starts N watchers / N boot syncs; design preferred one watcher | Documents one idempotent watcher per web worker and guards process-local duplicate starts |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 5 | Fixed | `backend/apps/keys/services/commands.py` | UI `upsert_user_named` can overwrite disk rows if called outside the view guard | Defense-in-depth: refuse overwrite when `source=disk` |
| 6 | Fixed | `backend/apps/web/templates/web/keys.html` | Soft-disabled disk keys still show as “Set”; `KeyMetadata.status` unused | Surface disabled status |
| 7 | Fixed | `key_sync.py` / `agent_sync.py` / `watch.py` | One-level `glob('*.yaml')` vs design “recursive watch” | Document v1 non-recursive |
| 8 | | sync | Duplicate `(owner, name)` across two disk files is last-wins | Optional conflict report |
| 9 | | `agent_parse.py` | Envelope-only edits still bump `source_rev` / may create redundant config revision | Harmless |

## Recommendations

- Fix Critical #1 before merge
- Fix Important #2 with a delete→re-add-unchanged beat regression test
- Add `close_old_connections()` in the watcher loop (#3)
- Decide single-watcher vs per-worker and record it (#4)
