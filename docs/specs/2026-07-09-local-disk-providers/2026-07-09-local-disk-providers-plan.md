# Local disk providers Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **10 of 10** · Item: **Local disk providers (keys + agent configs)**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks (then only check off completed items). Steps use checkbox (`- [ ]`) syntax. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Repackage local-disk key/data ingest into `libs/providers/{llm,key,data}` + `libs/file` + app-layer sync; remove `apps.local_disk`; close review findings (boot safety, beat re-enable, watcher DB connections, UI defense-in-depth).

**Architecture:** Django-free libs own protocols, YAML parse, and file hashing. `apps.keys` / `apps.agents` own ORM ingest. `apps.web` owns boot sync + watcher. Product rules unchanged (DB SoT, soft-disable, UI read-only).

**Tech Stack:** Django 5.2, PyYAML, pathlib, threading polling watcher.

**Branch:** `feat/2026-07-09-local-disk-providers`

**Design spec:** [`2026-07-09-local-disk-providers-design.md`](./2026-07-09-local-disk-providers-design.md)

**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

**Starting point:** First impl already on this branch (`apps.local_disk`, provenance fields, UI guards). This plan is a **refactor + review-fix** pass — do not re-implement product behavior from scratch.

---

## Step 0 — Pre-implementation (mandatory)

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-09-local-disk-providers
git branch --show-current
```

- [ ] **Step 0b: Revision template exists** — `2026-07-09-local-disk-providers-revision.md` (human notes already present; do not rewrite).

- [ ] **Step 0c: Commit updated design/plan if uncommitted**

```bash
git add docs/specs/2026-07-09-local-disk-providers/
git commit -m "docs(local-disk): revise design and plan for libs providers layout"
git fetch origin main && git rebase origin/main && git push
```

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …` (mutating: `./olib/scripts/orun …`)
- Scoped tests: `cd backend && ../.venv/bin/python manage.py test <label> -v1`
- Gate: `./olib/scripts/orunr py test-all` (ignore unrelated olib `snap` host failure if still present)
- **No compatibility re-exports:** update imports to canonical paths; **delete** old modules
- **Function documentation:** docstring on every new/changed function per `AGENTS.md`
- **Parproc naming:** no `error` / `exception` / `warning` / `notice` / `deprecated` in test names
- After each stage commit: `git fetch origin main && git rebase origin/main && git push`
- When applying revision items: only flip `- [ ]` → `- [x]` in `-revision.md` — no other edits
- Update `*-review.md` Status → `Fixed` when closing a review row

---

## Target file structure

```
backend/libs/
  file/                            # NEW
    __init__.py
    hashing.py                     # from apps.local_disk.hashing
    tests/test_hashing.py
  providers/
    llm/                           # MOVE current libs/providers/* here
      … (openai, anthropic, registry, …)
    key/                           # NEW
      __init__.py
      protocol.py                  # KeyProvider Protocol (optional thin)
      disk_parse.py                # from local_disk.key_parse (Django-free)
      tests/test_disk_parse.py
    data/                          # NEW
      __init__.py
      protocol.py                  # DataProvider Protocol (optional thin)
      agent_disk_parse.py          # envelope + body split (Django-free)
      tests/test_agent_disk_parse.py

backend/apps/
  keys/
    services/disk_sync.py          # NEW — ORM ingest using libs.providers.key
    services/commands.py           # refuse UI overwrite of source=disk
    services/queries.py            # already has source/status
    # owner resolve stays here (Django user)
  agents/
    services/disk_sync.py          # NEW — ORM ingest using libs.providers.data
    services/schedule_beat.py      # re-enable on disabled→active
  web/
    local_bootstrap.py             # NEW — settings paths, sync_all, watch, ready hooks
    apps.py                        # safe ready() / post_migrate
    templates/web/keys.html        # show disabled status
  local_disk/                      # DELETE entire app after move

backend/chief/settings.py          # CHIEF_LOCAL_DIR / CHIEF_LOCAL_WATCH (already)
docs/ARCHITECTURE.md
AGENTS.local.md                    # libs table
```

---

## R1 — Extract `libs/file`

**Files:** Create `libs/file/hashing.py` + tests; update callers later in R3/R4.

- [ ] **Step 1: Failing test** for `content_hash` / CRLF normalize under `libs/file/tests/`

- [ ] **Step 2: Implement** by moving logic from `apps.local_disk.hashing` (copy then switch imports in later tasks)

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(libs): add libs/file hashing helpers"
# fetch/rebase/push
```

---

## R2 — Move LLM providers to `libs/providers/llm`

**Files:** Move all current `libs/providers/*` (except new key/data) under `libs/providers/llm/`. Update every import site (`apps.runner`, tests, etc.). **Delete** old flat modules — no shims.

- [ ] **Step 1: Inventory imports**

```bash
rg -n "from libs.providers|import libs.providers" backend/
```

- [ ] **Step 2: Move package + fix imports + run provider/runner tests**

```bash
cd backend && ../.venv/bin/python manage.py test apps.runner.tests libs.providers -v1
```

(Adjust test discovery paths to match new package.)

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(providers): move LLM providers under libs/providers/llm"
```

---

## R3 — `libs/providers/key` + `apps.keys` disk sync

**Files:**
- Create `libs/providers/key/disk_parse.py` from `apps.local_disk.key_parse` (no `apps.keys` imports — validate type in app layer)
- Create `apps.keys.services.disk_sync` from `key_sync` + `owner` usage
- Point tests at new modules; delete old local_disk key modules when unused

- [ ] **Step 1: Port parse tests to libs (Django-free where possible)**

- [ ] **Step 2: Port sync tests to `apps.keys.tests`**

- [ ] **Step 3: Wire `upsert_user_named_from_disk` + soft_disable as today**

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(keys): disk key provider libs + apps.keys ingest"
```

---

## R4 — `libs/providers/data` + `apps.agents` disk sync

**Files:**
- Create `libs/providers/data/agent_disk_parse.py` — envelope strip only; validation via injected callable or return raw body for `apps.agents` to validate (avoid importing `apps.agents` from libs)
- Create `apps.agents.services.disk_sync` from `agent_sync`
- Fix **review #2:** on disabled→active, always `sync_agent_schedule_triggers`; add test delete→re-add unchanged

- [ ] **Step 1: Port parse + sync tests**

- [ ] **Step 2: Implement beat re-enable on reactivation**

```python
# In disk agent persist path, when previous status was DISABLED and file present:
agent.status = AgentStatus.ACTIVE
# … update fields …
sync_agent_schedule_triggers(agent.id)  # even if source_rev unchanged
```

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(agents): disk data provider libs + beat re-enable on reactivate"
```

---

## R5 — Boot/watch into `apps.web`; delete `apps.local_disk`

**Files:**
- `apps/web/local_bootstrap.py` — `resolve_local_root` (settings), `sync_all` (keys then agents), `PollingWatcher`, `maybe_start_local_disk`
- **Review #1:** skip ORM sync when `migrate`/`makemigrations`/`collectstatic` in argv; wrap sync so exceptions never abort `ready()`; prefer also hooking `post_migrate` for first sync after migrate
- **Review #3:** `close_old_connections()` at top of each watcher loop
- **Review #4:** document per-web-worker watcher; single-process lock; workers only if `CHIEF_LOCAL_WATCH`
- Remove `apps.local_disk` from `INSTALLED_APPS`; delete package
- Update `WebConfig.ready` / `RunnerConfig.ready` imports

- [ ] **Step 1: Tests for boot guards** (migrate argv → no sync; sync raises → ready continues)

- [ ] **Step 2: Move watch/bootstrap; fix connections**

- [ ] **Step 3: Delete `apps.local_disk`; fix remaining imports**

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(web): local disk boot/watch; remove apps.local_disk"
```

---

## R6 — Review minors + docs

- [ ] **Review #5:** `upsert_user_named` raises if existing row `source=disk`
- [ ] **Review #6:** `keys.html` shows disabled from `status`
- [ ] **Review #7:** Document non-recursive globs in ARCHITECTURE / design (already in design)
- [ ] Update `AGENTS.local.md` libs table: `providers/llm`, `providers/key`, `providers/data`, `file`
- [ ] Update `docs/ARCHITECTURE.md` libs section
- [ ] Mark matching items `[x]` in `-revision.md` (checkboxes only)
- [ ] Set Fixed in `-review.md` for closed rows

- [ ] **Commit**

```bash
git commit -m "fix(local-disk): UI defense-in-depth, disabled badge, docs for providers layout"
```

---

## R7 — Full gate

```bash
cd backend && ../.venv/bin/python manage.py test apps.keys.tests apps.agents.tests apps.web.tests apps.runner.tests -v1
./olib/scripts/orunr py test-all
```

- [ ] Fix regressions; commit fixups; push

---

## S_final — Code review (mandatory)

### Task: Code review

> **REQUIRED SKILL:** `superpowers/requesting-code-review`. Write/overwrite
> `2026-07-09-local-disk-providers-review.md` for the **refactor range** (or append a
> dated follow-up section). Do not auto-fix unless user asks.

- [ ] **Step 1:** Confirm tests pass
- [ ] **Step 2:** `BASE_SHA=$(git merge-base HEAD origin/main)` / `HEAD_SHA=$(git rev-parse HEAD)`
- [ ] **Step 3:** Dispatch reviewer
- [ ] **Step 4:** Write review file + summarize in chat
- [ ] **Step 5:** Track Fixed/Rejected from user feedback
- [ ] **Step 6:** Offer `superpowers/finishing-a-development-branch`

---

## Out of scope

- GitHub data provider
- Recursive directory trees
- System credentials on disk
- UI write-back to disk
- Renaming `CHIEF_LOCAL_DIR` layout

---

## Spec / revision coverage

| Requirement | Task |
|-------------|------|
| `libs/providers/llm` move | R2 |
| `libs/providers/key` + key ingest | R3 |
| `libs/providers/data` + agent ingest | R4 |
| `libs/file` | R1 |
| Remove `apps.local_disk` | R5 |
| Review #1 boot/migrate safety | R5 |
| Review #2 beat re-enable | R4 |
| Review #3 close_old_connections | R5 |
| Review #4 watcher process model | R5 |
| Review #5–#7 minors + docs | R6 |
| Revision.md items 1–4 | R1–R6 |
