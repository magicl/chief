# Obsidian vault service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-02-obsidian-vault-service/2026-08-02-obsidian-vault-service-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — under `/ship`, return to the ship skill for **S_final** (`superpowers/requesting-code-review`); do not run finishing menus from the executor.

**Goal:** Ship a separate Python vault service that supervises Obsidian Headless Sync (one checkout per vault), plus a Chief `obsidian` tool with path-gated list/read/write/append, lifecycle ensure/release, and Compose wiring.

**Architecture:** `services/obsidian/` owns Sync/headless, agent→vault bindings, first-sync gate, and file HTTP API (no Chief DB). Chief resolves Obsidian Sync secrets from `apps.keys` only at ensure time, talks to the service with Docker-injected inter-service auth, and exposes `libs/clients/obsidian` + `libs/tools/tools/obsidian.py`.

**Tech Stack:** Python 3 (FastAPI + uvicorn for the vault service), Node 22+ (`obsidian-headless` / `ob`), httpx client in Chief, Django materialize/delete hooks, Docker Compose

**Branch:** `feat/2026-08-02-obsidian-vault-service`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …` while iterating; `./olib/scripts/orunr py test-all` before S_final
- Vault service tests: `./olib/scripts/orunr py test "$PWD/services/obsidian"` after the `services/obsidian` PyRoot exists
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-02-obsidian-vault-service`; after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases (Chief/Django):** `OTestCase` / `OTransactionTestCase` only — never bare `unittest.TestCase`
- **Vault service tests:** use `unittest.TestCase` only inside `services/obsidian` (no Django). Do not import `olib.py.django.test.cases` there.
- Avoid parproc-highlighted words in test names (`error`, `exception`, `warning`, `deprecated`, …)
- Inter-service auth: env only (`OBSIDIAN_VAULT_URL`, `OBSIDIAN_VAULT_TOKEN`) — never `apps.keys`
- Obsidian Sync secrets: `apps.keys` type `obsidian` JSON — never inter-service env

## Normative contracts

### Obsidian Sync credential (`apps.keys`, type `obsidian`)

Plaintext secret is JSON:

```json
{
  "auth_token": "<token usable by ob login / headless>",
  "encryption_password": "<optional; required for E2E vaults>"
}
```

### Inter-service auth

- Header: `Authorization: Bearer <OBSIDIAN_VAULT_TOKEN>`
- Missing/wrong token → HTTP 401

### Vault service error body

```json
{"ok": false, "error": {"kind": "sync_pending"|"outside_root"|"not_found"|"forbidden"|"auth"|"config"|"unavailable", "message": "..."}}
```

`sync_pending` and `unavailable` are **retryable** for the Chief tool.

### HTTP routes (v1)

| Method | Path | Purpose |
|--------|------|---------|
| `PUT` | `/v1/agents/{agent_id}/vaults` | Ensure bindings (body below) |
| `DELETE` | `/v1/agents/{agent_id}/vaults` | Release all bindings for agent |
| `GET` | `/v1/vaults/{vault_id}/status` | Ready / sync status |
| `GET` | `/v1/agents/{agent_id}/files` | List (`path` query) |
| `GET` | `/v1/agents/{agent_id}/files/content` | Read (`path`) |
| `PUT` | `/v1/agents/{agent_id}/files/content` | Write (`path` + body text) |
| `POST` | `/v1/agents/{agent_id}/files/append` | Append (`path` + body text) |

Ensure body:

```json
{
  "bindings": [
    {
      "vault_id": "Personal",
      "roots": ["Journal"],
      "credential": {
        "auth_token": "...",
        "encryption_password": null
      }
    }
  ]
}
```

File ops require the agent binding; paths must stay under that binding’s roots.

## File map

| Path | Responsibility |
|------|----------------|
| `services/obsidian/` | Vault service package (API, bindings, paths, supervisor, Dockerfile) |
| `config.py` | Add `PyRoot('./services/obsidian')` |
| `backend/libs/clients/obsidian/` | Chief HTTP client |
| `backend/libs/tools/tools/obsidian.py` | Agent tool |
| `backend/apps/agents/tools_wiring.py` | Register tool |
| `backend/apps/agents/vault_lifecycle.py` | ensure/release orchestration (resolve secrets, call client) |
| `backend/apps/agents/materialize.py` / `delete.py` | Hooks |
| `backend/chief/settings.py` | `OBSIDIAN_VAULT_URL`, `OBSIDIAN_VAULT_TOKEN` |
| `infra/docker/docker-compose.yml` | `chief-obsidian` + env on backend/worker |
| `backend/apps/keys/credential_guides.py` | Sync/headless guide (replace Local REST API copy) |
| `docs/docs/agents.md` | Document `obsidian` tool |
| `docs/ARCHITECTURE.md` | Brief `services/obsidian` + Compose note |

---

## Stage A — Vault service core (no HTTP yet)

### Task 1: Package skeleton + path safety

**Files:**
- Create: `services/obsidian/pyproject.toml`
- Create: `services/obsidian/obsidian_vault/__init__.py`
- Create: `services/obsidian/obsidian_vault/paths.py`
- Create: `services/obsidian/obsidian_vault/tests/test_paths.py`
- Modify: `config.py` — add `PyRoot('./services/obsidian')` next to other roots

- [ ] **Step 1: Write failing path tests**

```python
import unittest
from pathlib import Path

from obsidian_vault.paths import PathGateError, resolve_under_roots


class TestPathResolve(unittest.TestCase):
    def test_accepts_path_inside_root(self) -> None:
        base = Path('/vaults/Personal')
        got = resolve_under_roots(base, roots=['Journal'], rel_path='Journal/2026-08-02.md')
        self.assertEqual(got, base / 'Journal' / '2026-08-02.md')

    def test_rejects_escape_via_dotdot(self) -> None:
        base = Path('/vaults/Personal')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='Journal/../Secrets/x.md')

    def test_rejects_outside_configured_roots(self) -> None:
        base = Path('/vaults/Personal')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='Other/note.md')
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./olib/scripts/orunr py test "$PWD/services/obsidian" -k=TestPathResolve
```

Expected: FAIL (module/root missing) until package + config root exist.

- [ ] **Step 3: Implement package + `resolve_under_roots`**

`resolve_under_roots(vault_root: Path, *, roots: list[str], rel_path: str) -> Path`:

- Normalize `rel_path` with posix rules; reject absolute paths and empty.
- Resolve against `vault_root`; require the resolved path is under `vault_root`.
- Require the relative path (from vault root) starts with one of `roots` as a path prefix (segment-aware: `Journal` matches `Journal/...`, not `Journalism/...`).
- Raise `PathGateError` with a safe message on failure.

Minimal `pyproject.toml` for a src-less package named `obsidian_vault` (name the import package `obsidian_vault`).

Add to `config.py` `@roots([...])`:

```python
PyRoot('./services/obsidian'),
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
./olib/scripts/orunr py test "$PWD/services/obsidian" -k=TestPathResolve
```

- [ ] **Step 5: Commit and sync**

```bash
git add services/obsidian config.py
git commit -m "feat(obsidian-vault): add path gate package skeleton"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

### Task 2: Bindings store + first-sync readiness

**Files:**
- Create: `services/obsidian/obsidian_vault/bindings.py`
- Create: `services/obsidian/obsidian_vault/tests/test_bindings.py`

- [ ] **Step 1: Write failing binding tests**

```python
import unittest
from obsidian_vault.bindings import VaultBindingStore, SyncPendingError


class TestVaultBindingStore(unittest.TestCase):
    def test_ensure_then_lookup_roots(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [
                {
                    'vault_id': 'Personal',
                    'roots': ['Journal'],
                    'credential': {'auth_token': 'tok', 'encryption_password': None},
                }
            ],
        )
        binding = store.get_binding('agent-1', vault_id='Personal')
        self.assertEqual(binding.roots, ['Journal'])
        self.assertFalse(binding.ready)

    def test_mark_ready_clears_sync_pending(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        store.mark_vault_ready('Personal')
        self.assertTrue(store.require_ready('agent-1', 'Personal'))

    def test_require_ready_raises_while_pending(self) -> None:
        store = VaultBindingStore()
        store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        with self.assertRaises(SyncPendingError):
            store.require_ready('agent-1', 'Personal')

    def test_refcount_teardown_when_last_agent_releases(self) -> None:
        store = VaultBindingStore()
        for agent in ('a', 'b'):
            store.ensure_agent(
                agent,
                [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
            )
        released = store.release_agent('a')
        self.assertEqual(released, [])
        released = store.release_agent('b')
        self.assertEqual(released, ['Personal'])
```

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test "$PWD/services/obsidian" -k=TestVaultBindingStore
```

- [ ] **Step 3: Implement `VaultBindingStore`**

In-memory store with:

- `ensure_agent(agent_id, bindings: list[dict])` — replace that agent’s desired set; update vault refcounts; return vault ids that need supervisor start
- `release_agent(agent_id) -> list[str]` — vault ids whose refcount hit 0
- `get_binding(agent_id, vault_id)`
- `mark_vault_ready(vault_id)` / `require_ready(agent_id, vault_id)` raising `SyncPendingError`
- Per-vault threading `Lock` accessor `lock_for(vault_id)`

Assume single vault per agent binding entry for v1 file ops: tool/client pick the binding’s `vault_id` from tool config (Chief passes `vault` as `vault_id`). File API uses agent_id + path; if an agent has multiple vaults, require query `vault_id` (document in API). For v1 Chief tool config has one `vault` per tool instance — client sends `vault_id` query on file routes.

Update routes contract: file routes take required query `vault_id`.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit and sync**

```bash
git add services/obsidian
git commit -m "feat(obsidian-vault): agent vault bindings and first-sync gate"
git fetch origin main && git rebase origin/main && git push
```

### Task 3: Filesystem file ops behind readiness + path gate

**Files:**
- Create: `services/obsidian/obsidian_vault/files.py`
- Create: `services/obsidian/obsidian_vault/tests/test_files.py`

- [ ] **Step 1: Write failing file op tests** (use `tempfile.TemporaryDirectory`)

Cover: list/read/write/append; sync_pending before ready; outside_root; append creates parents; write overwrite.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `VaultFileService`**

Constructor takes `VaultBindingStore` and `vault_root_for(vault_id) -> Path` callable.

Methods: `list_dir`, `read_text`, `write_text`, `append_text` — each:

1. `require_ready`
2. resolve path under roots
3. take `lock_for(vault_id)`
4. perform IO (UTF-8)

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit and sync**

```bash
git commit -m "feat(obsidian-vault): path-gated list read write append"
```

---

## Stage B — Supervisor + HTTP API

### Task 4: Headless supervisor protocol + fake

**Files:**
- Create: `services/obsidian/obsidian_vault/supervisor.py`
- Create: `services/obsidian/obsidian_vault/tests/test_supervisor.py`

- [ ] **Step 1: Write tests against a `FakeSupervisor`**

Protocol methods:

```python
class HeadlessSupervisor(Protocol):
    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None, roots_hint: list[str]) -> None: ...
    def stop_vault(self, vault_id: str) -> None: ...
    def is_initial_sync_complete(self, vault_id: str) -> bool: ...
```

`FakeSupervisor`: marks complete immediately (or after `complete(vault_id)` for pending tests). Real `ObsidianHeadlessSupervisor` shells out to `ob` (`sync-setup`, `sync --continuous`) under a data dir; if `ob` missing in unit tests, only Fake is used. Real supervisor unit-tested with monkeypatched subprocess.

- [ ] **Step 2–4: TDD implement Fake + real supervisor class that invokes `ob` via subprocess list args (no shell=True)**

Real supervisor behavior:

- Working tree: `{DATA_DIR}/vaults/{safe_vault_id}/`
- On ensure: login/token env as required by headless docs; `ob sync-setup --vault … --path …`; start `ob sync --continuous` as child; poll until first sync complete (status file or `ob sync-status` if available — implement with a clear hook `poll_ready` that Fake can override)
- On stop: terminate child, optionally rmtree tree when refcount 0 (caller decides)

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(obsidian-vault): headless supervisor protocol and fake"
```

### Task 5: FastAPI app + auth middleware

**Files:**
- Create: `services/obsidian/obsidian_vault/app.py`
- Create: `services/obsidian/obsidian_vault/auth.py`
- Create: `services/obsidian/obsidian_vault/main.py`
- Create: `services/obsidian/obsidian_vault/tests/test_api.py`
- Create: `services/obsidian/requirements.txt` (fastapi, uvicorn, httpx for TestClient)

- [ ] **Step 1: API tests with `TestClient`**

- 401 without bearer
- ensure → file write returns sync_pending until mark ready (wire FakeSupervisor that stays pending until test flips it)
- after ready, append + read roundtrip
- release tears down

Wire app factory `create_app(*, token: str, store, files, supervisor, vault_roots: Path)`.

- [ ] **Step 2–4: Implement routes matching the normative contract; map exceptions to JSON error bodies + appropriate HTTP codes (`409` or `503` for sync_pending — pick `503` with `kind=sync_pending`)**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(obsidian-vault): HTTP API with bearer auth"
```

### Task 6: Dockerfile + entrypoint

**Files:**
- Create: `services/obsidian/Dockerfile`
- Create: `services/obsidian/entrypoint.sh`

- [ ] **Step 1: Dockerfile**

- Base: python slim
- Install Node 22+
- `npm install -g obsidian-headless`
- Install Python deps from `requirements.txt`
- Copy package; `ENTRYPOINT` runs uvicorn `obsidian_vault.main:app`
- Env: `OBSIDIAN_VAULT_TOKEN`, `OBSIDIAN_VAULT_DATA=/data`, `PORT=8100`

- [ ] **Step 2: Commit**

```bash
git commit -m "feat(obsidian-vault): container image with Node and ob"
```

---

## Stage C — Chief client + settings

### Task 7: Settings + Obsidian vault HTTP client

**Files:**
- Modify: `backend/chief/settings.py` — add:

```python
OBSIDIAN_VAULT_URL = env.str('OBSIDIAN_VAULT_URL', default='')  # noqa: F405
OBSIDIAN_VAULT_TOKEN = env.str('OBSIDIAN_VAULT_TOKEN', default='')  # noqa: F405
```

- Create: `backend/libs/clients/obsidian/{__init__,errors,protocol,client,mock,config}.py`
- Create: `backend/libs/clients/obsidian/tests/test_client.py`

Client constructor:

```python
def __init__(self, *, base_url: str, token: str, transport=None, agent_id: str): ...
```

Methods: `ensure_vaults(bindings)`, `release_vaults()`, `list_dir(vault_id, path)`, `read_text`, `write_text`, `append_text`, map HTTP JSON failures to typed errors including `ObsidianSyncPendingError`, `ObsidianOutsideRootError`, etc.

Config helper `parse_obsidian_tool_config(config) -> ObsidianToolConfig` with fields `vault: str`, `roots: list[str]` (non-empty strings; segment-safe).

- [ ] TDD client against httpx MockTransport
- [ ] Commit: `feat(obsidian): Chief HTTP client and settings`

---

## Stage D — Tool

### Task 8: `obsidian` tool + registration

**Files:**
- Create: `backend/libs/tools/tools/obsidian.py`
- Create: `backend/libs/tools/tests/test_obsidian_tool.py`
- Modify: `backend/apps/agents/tools_wiring.py` — `register_tool('obsidian', ObsidianTool())`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py` — wire test mirroring dropbox

`ObsidianTool`:

- `name = 'obsidian'`
- `credential_type = 'obsidian'`
- `bind`: parse config; build client from `ctx.client_factories.get('obsidian')` or default factory reading `django.conf.settings.OBSIDIAN_VAULT_URL/TOKEN` + `str(ctx… agent id)` — **ToolContext must expose agent id**. Check `ToolContext` fields; if no agent_id, add optional `agent_id: str | None` to `ToolContext` and set it in `SessionRunner` / wiring tests.

Retry helper inside tool invoke for `ObsidianSyncPendingError` / unavailable: exponential backoff (e.g. 0.05, 0.1, 0.2… capped) with max wait ~30s in production; tests inject `sleep` and clock.

Functions: `list`, `read`, `write`, `append` with schemas; return `{ok: True, ...}` / `_failure` mapping.

- [ ] TDD tool tests with mock client that first raises sync_pending then succeeds
- [ ] Commit: `feat(obsidian): agent tool with sync_pending retry`

---

## Stage E — Lifecycle hooks

### Task 9: ensure/release on materialize and delete

**Files:**
- Create: `backend/apps/agents/vault_lifecycle.py`
- Create: `backend/apps/agents/tests/test_vault_lifecycle.py`
- Modify: `backend/apps/agents/materialize.py`
- Modify: `backend/apps/agents/delete.py`

`sync_obsidian_vaults(agent, spec)`:

1. Collect tool instances with `type == 'obsidian'` (after integration merge — use resolved spec tools).
2. For each, `parse_obsidian_tool_config`, resolve secret via `make_secret_supplier` / `resolve_secret` for user `agent.user_id`.
3. Parse JSON credential; build ensure payload.
4. If `OBSIDIAN_VAULT_URL` empty: no-op (log notice) so tests/dev without service still materialize — **unless** obsidian tools present and URL set.
5. Call client `ensure_vaults`. Prefer `transaction.on_commit` so DB materialize succeeds even if vault service is briefly down; record failures in logs. Design acceptance prefers ensure during materialize: use on_commit + raise/log. For v1: call inside `on_commit` and log failures without rolling back agent config (operator retries by re-saving). Document this in code comment.

`release_obsidian_vaults(agent_id)` on delete: `on_commit` after delete, call release (agent id known before delete).

- [ ] TDD with mocked client
- [ ] Commit: `feat(obsidian): ensure and release vaults on agent lifecycle`

---

## Stage F — Compose, credential guide, docs

### Task 10: Compose service + env examples

**Files:**
- Modify: `infra/docker/docker-compose.yml` — add `chief-obsidian`; pass `OBSIDIAN_VAULT_URL=http://chief-obsidian:8100` and shared `OBSIDIAN_VAULT_TOKEN` into backend, worker, beat (beat optional), and vault service
- Modify: `.env.local.example` — document `OBSIDIAN_VAULT_TOKEN=` (generate for compose)
- Modify: `backend/chief/tests/test_compose_config.py` — assert `chief-obsidian` service exists and backend receives `OBSIDIAN_VAULT_URL`
- Volume: `obsidian_vault_data:/data`

- [ ] TDD compose config assertions first
- [ ] Commit: `feat(compose): add chief-obsidian vault service`

### Task 11: Credential guide + operator docs + architecture

**Files:**
- Modify: `backend/apps/keys/credential_guides.py` — replace Local REST API steps with Headless Sync auth token + optional E2E password JSON shape
- Modify: `backend/apps/keys/tests/` if guides are tested
- Modify: `docs/docs/agents.md` — new `#### obsidian` section (config: `vault`, `roots`; functions; retry note; credential JSON)
- Modify: `docs/ARCHITECTURE.md` — short paragraph on `services/obsidian` and no-DB boundary
- Optional example under `backend/libs/agent_spec/examples/` if other tools have examples

- [ ] Commit: `docs: document obsidian tool and Sync credentials`

### Task 12: Full Python gate

- [ ] Run `./olib/scripts/orunr py test-all` — fix failures
- [ ] Commit only if fixes needed

---

## S_final — Code review (mandatory)

### Task 13: Code review

> Under `/ship`, the **ship** skill owns this step: run `superpowers/requesting-code-review`, write `*-review.md`, **fix actionable findings**, re-verify, then open the PR. Executors must **return here** without finishing menus.

**Files:** (review artifact)

- [ ] Confirm `./olib/scripts/orunr py test-all` passes
- [ ] `git fetch origin main`; compute `BASE_SHA` / `HEAD_SHA`
- [ ] Dispatch reviewer per `requesting-code-review` / `code-reviewer.md`
- [ ] Write `docs/specs/2026-08-02-obsidian-vault-service/2026-08-02-obsidian-vault-service-review.md`
- [ ] Fix or reject every finding; Status column `Fixed` / `Rejected`

---

## Out of scope

- Delete/move/rename APIs; binary files; Obsidian desktop CLI
- Per-agent checkouts; Git mirrors
- Vault service reading Postgres
- Journal-specific parsers
- Interactive Obsidian OAuth UI

## References

- Design: `docs/specs/2026-08-02-obsidian-vault-service/2026-08-02-obsidian-vault-service-design.md`
- Patterns: Dropbox client/tool, `materialize_agent_config`, `tools_wiring.py`
- Headless: https://obsidian.md/help/sync/headless
