# Local disk providers — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-09-local-disk-providers-design.md`](./2026-07-09-local-disk-providers-design.md)
**Plan:** [`2026-07-09-local-disk-providers-plan.md`](./2026-07-09-local-disk-providers-plan.md)
**Branch:** `feat/2026-07-09-local-disk-providers`

---

## Follow-up review (libs refactor) — 2026-07-10

**Review range:** `190d356..9a67baf`

### Assessment

**Ready to merge?** Yes (with optional follow-ups)

**Reasoning:** Target layout landed (`libs/providers/{llm,key,data}`, `libs/file`, app ingest, `apps.web.local_bootstrap`; `apps.local_disk` removed). Prior Critical/Important/Minor #1–#7 verified Fixed with tests. No new Critical issues. One Important follow-up (boot sync failure leaves files unloaded until edit/restart).

### Strengths

- Clean Django-free libs vs app ORM split; no re-export shims
- Prior review fixes re-verified (migrate argv / post_migrate / contained sync; beat re-enable; `close_old_connections`; watcher lock; upsert refuse; disabled badge; non-recursive docs)
- Secret hygiene + layered conflict/read-only guards
- Focused suite: 287 tests passed (`apps.keys|agents|web|runner` + provider lib tests)

### Issues

#### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

#### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 10 | | `apps/web/local_bootstrap.py` | If initial `sync_all` fails (e.g. DB not ready), root is not marked synced and watcher only reacts to *changes* — existing files stay unloaded until edit/restart | Bounded retry or periodic full resync |

#### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 11 | | `disk_sync.py` (keys/agents) | Per-file `except` omits `IntegrityError` — one unique-constraint race can truncate the rest of a directory batch | Add to caught tuple |
| 12 | | `libs/providers/data/agent_disk_parse.py` | `source_rev` hashes full file including envelope → envelope-only edits create config revisions | Hash body only (was prior #9) |
| 13 | | agents `config_source` | Bare `'disk'`/`'ui'` strings vs keys’ enum | Optional TextChoices |
| 14 | | `local_bootstrap.py` | Boot `mkdir` of `keys/`/`agents/` writes into operator dir | Drop or document |
| 15 | | key sync | Duplicate `(owner,name)` across two files is last-wins | Was prior #8; optional |

### Recommendations

- Track #10 for compose start-order robustness
- Fast-follow #11–#12 if cheap

---

## Original review (pre-refactor) — superseded

**Review range:** `190d356..4c67114` (2026-07-10)

### Assessment (historical)

**Ready to merge?** With fixes — addressed in refactor (see follow-up).

### Issues (historical statuses)

#### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `apps/web` bootstrap | ORM sync in `ready()` could break migrate | Skips unsafe argv; `post_migrate`; contains failures |

#### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 2 | Fixed | agent disk sync | Beat not re-enabled on unchanged re-add | `sync_agent_schedule_triggers` on disabled→active |
| 3 | Fixed | watcher | No `close_old_connections` | Fixed in `local_bootstrap` |
| 4 | Fixed | multi-worker | N watchers | Documented + process lock |

#### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 5 | Fixed | `upsert_user_named` | Could overwrite disk | Refuses `source=disk` |
| 6 | Fixed | `keys.html` | Disabled not shown | Disabled pill |
| 7 | Fixed | globs | Non-recursive vs design | Documented |
| 8 | | sync | Duplicate names last-wins | See follow-up #15 |
| 9 | | agent parse | Envelope bumps rev | See follow-up #12 |
