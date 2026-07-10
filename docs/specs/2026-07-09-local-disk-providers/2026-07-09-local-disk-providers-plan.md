# Local disk providers Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **10 of 10** · Item: **Local disk providers (keys + agent configs)**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Load and live-watch user credential YAML and agent config YAML under `CHIEF_LOCAL_DIR`, ingesting into the DB (runtime SoT) with disk provenance, soft-disable on delete, and UI read-only for disk-sourced rows.

**Architecture:** New Django app `apps.local_disk` owns path helpers, YAML parse, key/agent sync, and a debounced FS watcher. Provenance/status fields live on `UserCredential` and `Agent`. Boot sync (keys then agents) + watcher start from `WebConfig.ready()` when `CHIEF_LOCAL_DIR` is set; workers opt in via `CHIEF_LOCAL_WATCH=1`. Consumers always resolve from the DB.

**Tech Stack:** Django 5.2, PyYAML (already present), pathlib, threading + polling watcher (v1 — no new FS dependency required).

**Branch:** `feat/2026-07-09-local-disk-providers`

**Design spec:** [`2026-07-09-local-disk-providers-design.md`](./2026-07-09-local-disk-providers-design.md)

**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-09-local-disk-providers || git checkout -b feat/2026-07-09-local-disk-providers
git branch --show-current   # must print feat/2026-07-09-local-disk-providers
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Ensure review template exists**

`docs/specs/2026-07-09-local-disk-providers/2026-07-09-local-disk-providers-revision.md` — leave empty.

- [ ] **Step 0c: Commit plan (if uncommitted)**

```bash
git add docs/specs/2026-07-09-local-disk-providers/ docs/epics/2026-07-03-inbox-cleanup.md
git commit -m "docs(local-disk): add implementation plan and revision template"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

Skip 0c if already committed.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …` (mutating: `./olib/scripts/orun …` for `makemigrations` / `py sync`)
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped `./olib/scripts/orunr py test backend/<path>` while iterating)
- Django migrations: `./olib/scripts/orun django manage makemigrations keys` / `agents` — never hand-write migration file bodies
- Test bases: `OTestCase` / `OTransactionTestCase` from `olib.py.django.test.cases`
- **Parproc naming:** never use `error`, `exception`, `warning`, `notice`, `deprecated` in test names
- **Function documentation:** every new/changed function/method gets a brief docstring per [`AGENTS.md`](../../AGENTS.md)
- **No compatibility re-exports:** update imports to canonical modules; delete replaced files
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts
- Never log secret `value` contents — only paths, names, types, owners

---

## File structure

```
backend/
  apps/local_disk/                 # NEW
    __init__.py
    apps.py
    paths.py                       # resolve root, keys_dir, agents_dir
    hashing.py                     # sha256 content hash
    owner.py                       # resolve Django user by username/email
    key_parse.py                   # parse key YAML → KeyDiskSpec
    agent_parse.py                 # split envelope + AgentConfigSpec body
    key_sync.py                    # sync_keys_dir / sync_key_path / disable missing
    agent_sync.py                  # sync_agents_dir / sync_agent_path / disable missing
    sync.py                        # sync_all (keys then agents)
    watch.py                       # debounced poller / watcher thread
    bootstrap.py                   # maybe_start_local_disk() for AppConfig.ready
    tests/
      __init__.py
      test_paths.py
      test_key_parse.py
      test_key_sync.py
      test_agent_parse.py
      test_agent_sync.py
      test_sync_all.py
      test_watch.py
  apps/keys/
    models.py                      # + source, source_path, source_rev, status
    services/commands.py           # upsert sets source=db; disk upsert helper
    services/queries.py            # skip status=disabled; expose source on metadata
    migrations/                    # generated
  apps/agents/
    models.py                      # + source_path, status (AgentStatus)
    ingest.py                      # pass source_path on create
    services/config_sync.py        # config_source_label includes Disk
    services/schedule_beat.py      # skip/disable beat for disabled agents
    migrations/                    # generated
  apps/runner/
    scheduling.py                  # _active_triggers + dispatch skip disabled agents
    start.py                       # start_manual_session rejects disabled
  apps/web/
    apps.py                        # ready() → maybe_start_local_disk
    views.py                       # block disk key mutate; start rejects disabled
    config_views.py                # block disk agent save/mutate/profile
    templates/web/                 # badges / read-only hints as needed
  chief/settings.py                # CHIEF_LOCAL_DIR, CHIEF_LOCAL_WATCH
.env.local.example
examples/local/keys/…              # sample YAMLs (fake secrets)
examples/local/agents/…
docs/ARCHITECTURE.md               # short paragraph
```

---

## S1 — App scaffolding + settings + paths + hash

**Files:**
- Create: `backend/apps/local_disk/` package as above (`paths.py`, `hashing.py`, `apps.py`, `tests/test_paths.py`)
- Modify: `backend/chief/settings.py` — add settings
- Modify: `backend/chief/settings.py` `INSTALLED_APPS` — add `'apps.local_disk'` after `apps.keys`

- [ ] **Step 1: Write failing path/hash tests**

```python
# backend/apps/local_disk/tests/test_paths.py
from pathlib import Path
from django.test import override_settings
from olib.py.django.test.cases import OTestCase
from apps.local_disk.hashing import content_hash
from apps.local_disk.paths import agents_dir, keys_dir, resolve_local_root


class TestLocalDiskPaths(OTestCase):
    def test_content_hash_is_stable_sha256_prefix(self) -> None:
        self.assertEqual(content_hash('a\nb'), content_hash('a\nb'))
        self.assertTrue(content_hash('x').startswith('sha256:'))

    def test_content_hash_normalizes_crlf(self) -> None:
        self.assertEqual(content_hash('a\r\nb'), content_hash('a\nb'))

    @override_settings(CHIEF_LOCAL_DIR='')
    def test_resolve_root_unset_returns_none(self) -> None:
        self.assertIsNone(resolve_local_root())

    @override_settings(CHIEF_LOCAL_DIR='/tmp/chief-local-test')
    def test_resolve_root_and_subdirs(self) -> None:
        root = resolve_local_root()
        assert root is not None
        self.assertEqual(root, Path('/tmp/chief-local-test').resolve())
        self.assertEqual(keys_dir(), root / 'keys')
        self.assertEqual(agents_dir(), root / 'agents')
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
./olib/scripts/orunr py test backend/apps/local_disk/tests/test_paths.py -v
```

- [ ] **Step 3: Implement scaffolding**

`hashing.py`:

```python
"""Stable content hashing for disk provider change detection."""

from __future__ import annotations

import hashlib


def normalize_bytes(raw: str | bytes) -> bytes:
    """Normalize text to UTF-8 LF for stable hashing."""
    if isinstance(raw, bytes):
        text = raw.decode('utf-8')
    else:
        text = raw
    return text.replace('\r\n', '\n').encode('utf-8')


def content_hash(raw: str | bytes) -> str:
    """Return ``sha256:<hex>`` for normalized file contents."""
    digest = hashlib.sha256(normalize_bytes(raw)).hexdigest()
    return f'sha256:{digest}'
```

`paths.py`:

```python
"""Resolve CHIEF_LOCAL_DIR and standard subdirectories."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def resolve_local_root() -> Path | None:
    """Return absolute local root, or None when unset/blank."""
    raw = getattr(settings, 'CHIEF_LOCAL_DIR', '') or ''
    raw = str(raw).strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def keys_dir() -> Path | None:
    """Return ``<root>/keys`` when root is configured."""
    root = resolve_local_root()
    return None if root is None else root / 'keys'


def agents_dir() -> Path | None:
    """Return ``<root>/agents`` when root is configured."""
    root = resolve_local_root()
    return None if root is None else root / 'agents'
```

`apps.py`: standard `AppConfig` for `apps.local_disk`.

`settings.py` additions:

```python
CHIEF_LOCAL_DIR = env.str('CHIEF_LOCAL_DIR', default='')  # noqa: F405
CHIEF_LOCAL_WATCH = env.bool('CHIEF_LOCAL_WATCH', default=False)  # noqa: F405
```

Register `'apps.local_disk'` in `INSTALLED_APPS` after `'apps.keys'`.

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/local_disk backend/chief/settings.py
git commit -m "feat(local-disk): add app scaffolding, settings, and path helpers"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

## S2 — UserCredential provenance + resolve skip disabled

**Files:**
- Modify: `backend/apps/keys/models.py`
- Modify: `backend/apps/keys/services/commands.py`, `queries.py`
- Generate migration via Django
- Test: `backend/apps/keys/tests/test_queries.py`, `test_commands.py`, `test_models.py`

- [ ] **Step 1: Write failing tests for status/source**

Add tests asserting:
1. New `UserCredential` fields exist with defaults `source='db'`, `status='active'`, empty `source_path`/`source_rev`.
2. `resolve_secret` raises `KeyNotFoundError` when user credential `status='disabled'` (treat as absent — then fall through to system or miss).
3. `upsert_user_named` leaves `source='db'` and `status='active'`.

Avoid parproc-banned words in test names (`test_resolve_skips_disabled_user_credential`).

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Model fields**

On `UserCredential`:

```python
class CredentialSource(models.TextChoices):
    DB = 'db', 'Database'
    DISK = 'disk', 'Disk'


class CredentialStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'

# fields:
source = models.CharField(max_length=16, choices=CredentialSource.choices, default=CredentialSource.DB)
source_path = models.CharField(max_length=512, blank=True, default='')
source_rev = models.CharField(max_length=128, blank=True, default='')
status = models.CharField(max_length=16, choices=CredentialStatus.choices, default=CredentialStatus.ACTIVE)
```

Update `resolve_secret` user branch: only use row when `status == ACTIVE`.

Extend `KeyMetadata` with optional `source: str = 'db'` and `status: str = 'active'` (or keep separate query helper — include on metadata so UI can badge). Update `_user_metadata`.

Ensure `upsert_user_named` defaults keep `source=db` (explicitly set on create/update so a disk→db takeover path is intentional elsewhere — UI upsert must not change a disk row; S7 blocks UI).

- [ ] **Step 4: Makemigrations + migrate**

```bash
./olib/scripts/orun django manage makemigrations keys
./olib/scripts/orunr py test backend/apps/keys/tests/ -v
```

- [ ] **Step 5: Commit and sync**

```bash
git commit -m "feat(keys): add disk provenance and soft-disable status"
# fetch/rebase/push
```

---

## S3 — Key YAML parse + disk sync

**Files:**
- Create: `key_parse.py`, `owner.py`, `key_sync.py`, tests
- Modify: `commands.py` — add `upsert_user_named_from_disk(...)` that sets provenance

- [ ] **Step 1: Failing parse tests**

```python
# name defaults to stem; owner/type/value required; unknown type fails
```

Parse result dataclass:

```python
@dataclass(frozen=True)
class KeyDiskFile:
    name: str
    type: str
    owner: str
    value: str
    source_path: str  # relative e.g. keys/work-gmail.yaml
    source_rev: str
```

- [ ] **Step 2: Failing sync tests** (temp dir + `override_settings(CHIEF_LOCAL_DIR=...)`)

Cases:
1. Valid file → creates encrypted `UserCredential` with `source=disk`, `status=active`
2. Content change → updates ciphertext + `source_rev`
3. Conflict with existing `source=db` same `(user,name)` → sync failure recorded; DB unchanged
4. File removed after disk bind → `status=disabled`
5. Missing owner user → skip file; no DB write
6. Invalid YAML → skip; no disable of unrelated keys

- [ ] **Step 3: Implement parse + owner resolve**

`owner.py`: resolve by `username` exact, else email if unique; else raise/return None for sync skip.

`key_parse.py`: load YAML mapping; require `type`, `value`, `owner`; default `name` to stem.

`key_sync.py`:

```python
def sync_key_path(path: Path, *, root: Path) -> SyncItemResult: ...
def sync_keys_dir() -> SyncReport: ...
def soft_disable_missing_disk_keys(*, present_paths: set[str]) -> int: ...
```

Use existing encrypt path. Disk upsert must set `source=disk`, `source_path`, `source_rev`, `status=active`.

Never overwrite when existing row has `source != disk`.

- [ ] **Step 4: Tests PASS + commit**

```bash
git commit -m "feat(local-disk): sync user credentials from keys/*.yaml"
```

---

## S4 — Agent provenance + skip disabled

**Files:**
- Modify: `agents/models.py`, `ingest.py`, `scheduling.py`, `start.py`, `schedule_beat.py`
- Migration via Django
- Tests: model + scheduling + manual start

- [ ] **Step 1: Failing tests**

1. `Agent` has `status` default `active`, `source_path` blank
2. `_active_triggers` excludes `agent__status=disabled`
3. `dispatch_schedule_trigger` no-ops / disables beat for disabled agent
4. `start_manual_session` raises `StartSessionError` when agent disabled
5. `sync_schedule_trigger` / beat sync disables periodic task when agent disabled

- [ ] **Step 2: Implement fields**

```python
class AgentStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'

# on Agent:
source_path = models.CharField(max_length=512, blank=True, default='')
status = models.CharField(max_length=16, choices=AgentStatus.choices, default=AgentStatus.ACTIVE)
```

`create_agent_from_spec(..., source_path: str = '')` sets field.

`_active_triggers`: add `agent__status=AgentStatus.ACTIVE` (import `AgentStatus`).

`start_manual_session`: reject disabled agents.

`schedule_beat.sync_schedule_trigger`: if agent disabled → `disable_schedule_trigger_beat`.

- [ ] **Step 3: makemigrations agents + tests PASS + commit**

```bash
git commit -m "feat(agents): add status and source_path for disk providers"
```

---

## S5 — Agent envelope parse + disk sync

**Files:**
- Create: `agent_parse.py`, `agent_sync.py`, tests
- Modify: `config_sync.config_source_label` for `disk`

- [ ] **Step 1: Failing parse tests**

Envelope fields `owner` (required), `identifier` (default stem), `name` (default identifier). Remaining body validates as agent YAML via existing `validate_agent_config_yaml` / `load_spec` path. Envelope keys stripped before validate.

- [ ] **Step 2: Failing sync tests**

1. Create → `config_source='disk'`, `source_path`, new config revision, hash `source_rev`
2. Content change → new `AgentConfig` row; rematerialize
3. Conflict with `config_source != 'disk'` → skip/fail item; no overwrite
4. Delete file → `status=disabled` + beat disabled
5. Bad YAML → skip; last good config remains active

- [ ] **Step 3: Implement**

Use `create_agent_from_spec` / `persist_agent_config` with `config_source='disk'`, `dirty=False`, `source_rev=content_hash`, `raw_yaml` = body-only YAML preferred (or full file with envelope removed for round-trip).

On soft-disable: set `Agent.status=disabled`; call beat disable for all schedule triggers on that agent.

- [ ] **Step 4: PASS + commit**

```bash
git commit -m "feat(local-disk): sync agents from agents/*.yaml"
```

---

## S6 — sync_all + boot watcher

**Files:**
- Create: `sync.py`, `watch.py`, `bootstrap.py`, tests
- Modify: `apps/web/apps.py` `ready()`

- [ ] **Step 1: Failing tests**

1. `sync_all` runs keys then agents (mock or order assertion via side effects)
2. Missing root → no-op report / no exception
3. Watcher debounce: rapid writes coalesce (test by calling `handle_fs_event` / `sync_path` API with fake clock or short debounce override)
4. `maybe_start_local_disk` respects unset dir; starts sync when root exists

- [ ] **Step 2: Implement**

```python
# sync.py
def sync_all() -> SyncReport:
    """Ingest keys then agents from CHIEF_LOCAL_DIR when configured."""
    ...

# watch.py — polling thread v1
# - interval ~1s scan of keys/ and agents/ mtimes+hashes OR watchdogs-free poll of file set
# - debounce 300ms per path via pending dict + timer
# - on delete: soft-disable path identity

# bootstrap.py
def maybe_start_local_disk(*, force_watch: bool | None = None) -> None:
    """Run initial sync; start watcher in web (always if root set) or when CHIEF_LOCAL_WATCH."""
```

`WebConfig.ready`:

```python
def ready(self) -> None:
    # Avoid double-start under autoreload parent if needed (django typical pattern)
    from apps.local_disk.bootstrap import maybe_start_local_disk
    maybe_start_local_disk()
```

Also call from `apps.local_disk` only if you prefer — design says web process; put primary hook in `WebConfig.ready`. For Celery, document `CHIEF_LOCAL_WATCH=1` and hook from `chief/celery.py` worker_ready or `RunnerConfig.ready` when flag set.

Create `keys/` and `agents/` under existing root if missing; if root path missing → log caution-level message (use logger.warning carefully — message text can say "missing" without banned test names).

- [ ] **Step 3: PASS + commit**

```bash
git commit -m "feat(local-disk): boot sync and debounced directory watch"
```

---

## S7 — UI read-only for disk sources

**Files:**
- Modify: `config_views.py`, `views.py` (keys + start), templates if needed
- Modify: `queries.py` / `config_sync.py` badges
- Tests: web tests

- [ ] **Step 1: Failing tests**

1. `agent_config_save` / mutate / profile update → 403 or BadRequest when `config_source == 'disk'`
2. Keys add/replace same name for disk-sourced credential → BadRequest
3. Keys delete for disk-sourced → BadRequest
4. Editor context includes `read_only: true` + source label `Disk` + `source_path`
5. Manual start for disabled agent → bad request / message (already service-layer)

- [ ] **Step 2: Implement guards**

Helper `assert_agent_writable(agent)` / `assert_credential_writable(row)`.

Update keys template to show source badge and hide write controls for disk rows.

Agent editor: when read-only, disable Save button / show banner (minimal HTML/Jinja change).

- [ ] **Step 3: PASS + commit**

```bash
git commit -m "feat(web): make disk-sourced keys and agents read-only in UI"
```

---

## S8 — Docs and examples

**Files:**
- `.env.local.example`
- `examples/local/keys/example-openai.yaml` (fake value)
- `examples/local/agents/minimal-disk.yaml` (envelope + minimal spec)
- `docs/ARCHITECTURE.md` paragraph

- [ ] **Step 1: Add env docs**

```bash
# Local disk providers (optional): keys/ and agents/ YAML ingested into DB
CHIEF_LOCAL_DIR=
# Set true on Celery workers if web is not running the watcher
CHIEF_LOCAL_WATCH=false
```

- [ ] **Step 2: Examples without real secrets**

- [ ] **Step 3: ARCHITECTURE note** — DB SoT; disk provider ingest; UI read-only

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(local-disk): document CHIEF_LOCAL_DIR layout and examples"
```

---

## S9 — Full gate

- [ ] **Step 1: Run full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: Fix any failures; commit fixups; push**

---

## S_final — Code review (mandatory)

### Task: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch reviewer via `code-reviewer.md`. Write `*-review.md` per `review-file-template.md`. Do not fix unless user asks.

- [ ] **Step 1: Confirm tests pass** — `./olib/scripts/orunr py test-all`
- [ ] **Step 2: Get git range** — `BASE_SHA=$(git merge-base HEAD origin/main)` / `HEAD_SHA=$(git rev-parse HEAD)`
- [ ] **Step 3: Run code review** (subagent)
- [ ] **Step 4: Write `2026-07-09-local-disk-providers-review.md` and report in chat**
- [ ] **Step 5: Track feedback** — Status Fixed/Rejected
- [ ] **Step 6: Human handoff** — `superpowers/finishing-a-development-branch`

---

## Out of scope

- GitHub provider
- System credentials on disk
- UI write-back to disk
- Encrypting files on disk
- Multi-root paths

---

## Spec coverage checklist

| Design requirement | Task |
|--------------------|------|
| `CHIEF_LOCAL_DIR` + keys/agents | S1, S8 |
| Key YAML + owner required | S3 |
| User credentials; soft-disable | S2, S3 |
| Agent envelope + create/update | S4, S5 |
| DB SoT / conflict rules | S3, S5 |
| Watch + boot sync order | S6 |
| UI read-only | S7 |
| Skip disabled resolve/dispatch | S2, S4 |
| Docs/examples | S8 |
| Code review | S_final |
