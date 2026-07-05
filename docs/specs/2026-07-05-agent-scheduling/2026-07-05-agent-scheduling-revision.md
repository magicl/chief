# Agent scheduling — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

Schedule triggers moved from a 60 s global scan to **per-trigger `django-celery-beat`
`PeriodicTask` rows** (UTC crontab). Config save and trigger status changes sync beat
tasks; migration `0006_sync_schedule_beat_tasks` backfills existing agents.

## Items to address

- [x] Function documentation per `AGENTS.md` on new/changed code — acceptable for v1
- [x] Celery beat intervals tuned for dev vs prod (document in revision if changed) — unchanged platform intervals; schedule crons are per-trigger in DB
- [ ] Example spec `queue-echo.yaml` exercises queue trigger in manual testing
- [x] `cron_matches_minute` / missed ticks — replaced with django-celery-beat per-trigger crontab (Celery computes next fire time)
- [ ] I get a migration error when starting the backend — if you applied the old reverted `0005_trigger_last_fired_at` migration locally, roll back agents to `0004` and re-migrate; fresh DBs apply `0005` + `0006` + `django_celery_beat` cleanly

## Schema v2 (trigger prompts)

- **`schema_version: 2`** — mandatory `prompt` on non-manual triggers.
- **Migration `002_trigger_prompts`** upgrades v1 specs in memory:
  - `schedule` → `Scheduled run started. Execute your configured tasks.`
  - `queue` → `Process this queue item.`
  - `agent` → `Agent trigger run started.`
- **`max_sessions`**: manual always `null`; schedule/queue omit → `1`; explicit `null` → unlimited concurrency.
- **Automated sessions** (schedule/queue triggers) terminate at **`waiting`** → **`done`** so `max_sessions` slots free for the next dispatch; manual sessions stay chat-able in `waiting`.
- Materialized trigger rows without `prompt` still dispatch via runtime fallback until config is re-saved at v2.
