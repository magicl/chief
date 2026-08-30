# Obsidian list/read during first sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-30-obsidian-read-during-first-sync/2026-08-30-obsidian-read-during-first-sync-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Let Obsidian `list` and `read` inspect a partial checkout after sync starts while keeping `write` and `append` gated on first full Sync, with all tool failures returned immediately.

**Architecture:** Add an explicit supervisor sync state (`not_started`, `syncing`, `partial`, `failed`, `ready`) guarded inside each supervisor implementation. `VaultFileService` receives a read-only state callback: reads permit syncing/partial/ready, writes additionally require the existing store `ready`; the supervisor owns short state transitions while one-shot subprocess waits happen outside its state lock. Chief's tool dispatch calls the client once with no retry helper.

**Tech Stack:** Python 3.13, FastAPI, `httpx`, `threading`, Django-free tool/client libraries, `orunr` quality gates

**Branch:** `feat/2026-08-30-obsidian-read-during-first-sync`

---

## Conventions

- Commands run from the exact repository/worktree root with `./olib/scripts/orunr …`.
- Use scoped tests while iterating; final gate is `./olib/scripts/orunr py test-all`.
- **Git:** this plan commits on `main`; implementation uses the branch above, and after each PR-ready stage commit runs `git fetch origin main && git rebase origin/main && git push`.
- **TDD:** add each behavior test first, run it and observe the expected failure, then write the minimum production code.
- **Function documentation:** per `AGENTS.md`, add or update a brief docstring for every function/method materially changed; explain state and race assumptions where they are non-obvious.
- **No compatibility re-exports:** update canonical imports directly; do not add bridge modules.
- **Test bases:** do not introduce new bare `unittest.TestCase` classes. Add methods to the existing standalone vault-service test classes; backend tests remain on `OTestCase`.
- **Test names:** avoid `error`, `exception`, `warning`, `deprecated`, and `deprecation`.
- **Final task:** code review through `superpowers/requesting-code-review`; `/ship` owns fixing every actionable finding before PR.

---

### Task 1: Model first-sync lifecycle without holding a lock across `ob`

**Files:**
- Modify: `services/obsidian/obsidian_vault/supervisor.py`
- Test: `services/obsidian/obsidian_vault/tests/test_supervisor.py`

- [ ] **Step 1: Write failing state-transition tests**

Add tests to the existing fake and real supervisor test classes for this public contract:

```python
from obsidian_vault.supervisor import VaultSyncState

def test_unknown_vault_reports_not_started(self) -> None:
    supervisor = FakeSupervisor()
    self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)

def test_pending_fake_reports_partial(self) -> None:
    supervisor = FakeSupervisor(auto_complete=False)
    supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

def test_nonzero_setup_records_failed_state(self) -> None:
    self.factory.fail_on('sync-setup')
    with self.assertRaises(HeadlessSyncError):
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)

def test_timeout_records_partial_state(self) -> None:
    self.factory.hang_on('sync-setup')
    self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

def test_stop_resets_sync_state(self) -> None:
    self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    self.supervisor.stop_vault('Personal')
    self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)
```

Also add a blocking fake process/factory test that starts `ensure_vault` on a thread, waits until `wait()` has been entered, asserts `sync_state(...) == SYNCING`, and calls a concurrent `ensure_vault`. The concurrent call must return promptly without starting a second one-shot. Release the fake process and assert the owner thread completes as `READY`.

- [ ] **Step 2: Run supervisor tests and verify RED**

```bash
./olib/scripts/orunr py test services/obsidian/obsidian_vault/tests/test_supervisor.py
```

Expected: failure because `VaultSyncState` / `sync_state` do not exist and concurrent start is not internally guarded.

- [ ] **Step 3: Add the supervisor state machine**

In `supervisor.py`, add:

```python
from enum import Enum
import threading

class VaultSyncState(str, Enum):
    """Describe whether a vault checkout may be read or written."""

    NOT_STARTED = 'not_started'
    SYNCING = 'syncing'
    PARTIAL = 'partial'
    FAILED = 'failed'
    READY = 'ready'
```

Extend `HeadlessSupervisor` with:

```python
def sync_state(self, vault_id: str) -> VaultSyncState:
    """Return the current first-sync lifecycle state for vault_id."""
```

For both `FakeSupervisor` and `ObsidianHeadlessSupervisor`, protect state/process maps with a short `threading.Lock`. Required transitions:

- unknown/stopped → `NOT_STARTED`
- immediately before launching setup → `SYNCING`
- setup or initial-sync timeout → `PARTIAL`
- non-zero setup or initial sync → `FAILED`, then raise the existing `HeadlessSyncError`
- successful marker write + continuous child start → `READY`
- stop → `NOT_STARTED`

`ensure_vault` must mark `SYNCING` under the lock, release it before `_run_one_shot`, and finalize under the lock. A concurrent ensure seeing `SYNCING` or `READY` returns without starting another process. Before finalizing, confirm the state is still `SYNCING`; if `stop_vault` changed it, do not create a ready marker or continuous child. Keep subprocess waits outside the state lock so `sync_state` and file reads are never blocked by `ob`.

- [ ] **Step 4: Run supervisor tests and verify GREEN**

```bash
./olib/scripts/orunr py test services/obsidian/obsidian_vault/tests/test_supervisor.py
```

Expected: all supervisor tests pass, including state, timeout/failure, stop, retry-after-timeout, and concurrent ensure.

- [ ] **Step 5: Commit and synchronize the supervisor chunk**

```bash
git add services/obsidian/obsidian_vault/supervisor.py services/obsidian/obsidian_vault/tests/test_supervisor.py
git commit -m "feat(obsidian): track first-sync lifecycle state"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Stop on rebase conflicts.

---

### Task 2: Split read and write gates in the vault file service

**Files:**
- Modify: `services/obsidian/obsidian_vault/files.py`
- Modify: `services/obsidian/obsidian_vault/main.py`
- Modify: `services/obsidian/obsidian_vault/app.py`
- Modify: `services/obsidian/obsidian_vault/reconcile.py`
- Test: `services/obsidian/obsidian_vault/tests/test_files.py`
- Test: `services/obsidian/obsidian_vault/tests/test_api.py`
- Test: `services/obsidian/obsidian_vault/tests/test_reconcile.py`

- [ ] **Step 1: Write failing file-service tests for partial reads and gated writes**

Construct `VaultFileService` with `sync_state_for=supervisor.sync_state`. Replace the old pre-ready read/list assertions with:

```python
def test_read_during_first_sync_returns_partial_file(self) -> None:
    self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    (self.vault_root / 'Journal').mkdir()
    (self.vault_root / 'Journal' / 'note.md').write_text('partial', encoding='utf-8')
    self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/note.md'), 'partial')

def test_list_during_first_sync_returns_partial_entries(self) -> None:
    self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    (self.vault_root / 'Journal').mkdir()
    (self.vault_root / 'Journal' / 'note.md').write_text('partial', encoding='utf-8')
    self.assertEqual(self.files.list_dir('agent-1', 'Personal', 'Journal'), ['note.md'])

def test_write_during_first_sync_raises_sync_pending(self) -> None:
    self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
    with self.assertRaises(SyncPendingError):
        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'blocked')
```

Add cases for:

- bound but `NOT_STARTED`: list/read raise `SyncPendingError`
- `FAILED`: list/read/write/append raise a new `VaultUnavailableError`
- `PARTIAL` from timeout: list/read allowed; write/append remain `SyncPendingError`
- read/list do not acquire the store's file lock (spy on `lock_for`), while write/append still do
- outside-root and missing-path behavior remains unchanged during `PARTIAL`

- [ ] **Step 2: Run file-service tests and verify RED**

```bash
./olib/scripts/orunr py test services/obsidian/obsidian_vault/tests/test_files.py
```

Expected: constructor/signature and behavior failures because all operations still call `require_ready` and take the same lock.

- [ ] **Step 3: Implement separate read and write contexts**

Update the service constructor and helpers:

```python
class VaultUnavailableError(Exception):
    """Raised when a hard first-sync failure makes the checkout unusable."""

def __init__(
    self,
    store: VaultBindingStore,
    vault_root_for: Callable[[str], Path],
    sync_state_for: Callable[[str], VaultSyncState],
) -> None:
    """Store binding, checkout-path, and first-sync-state collaborators."""

def _binding_context(
    self,
    agent_id: str,
    vault_id: str,
    *,
    require_ready: bool,
) -> tuple[Path, list[str]]:
    """Authorize a binding and enforce the read or write first-sync gate."""
```

Gate in this order:

1. Resolve the agent binding (preserves unbound-agent → unavailable mapping).
2. Read `sync_state_for(vault_id)`.
3. `FAILED` → `VaultUnavailableError`.
4. `NOT_STARTED` → `SyncPendingError`.
5. If `require_ready`, call `store.require_ready` (so `SYNCING`/`PARTIAL` writes remain pending).
6. Return the checkout and roots.

`list_dir` / `read_text` call with `require_ready=False` and perform descriptor-safe IO without `store.lock_for`. `write_text` / `append_text` call with `require_ready=True` and retain the per-vault file lock around IO.

Wire `main.py` with:

```python
_files = VaultFileService(
    _store,
    vault_root_for=_supervisor.vault_dir,
    sync_state_for=_supervisor.sync_state,
)
```

Map `VaultUnavailableError` explicitly to HTTP 500 / `unavailable` in `app.py`.

- [ ] **Step 4: Add API-level failing tests, then remove long external ensure locks**

Before changing orchestration, add API tests using `FakeSupervisor(auto_complete=False)` and a matching file service state callback:

- after ensure, seed a partial file directly and verify GET list/read return 200
- PUT/append before `ready` return 503 / `sync_pending`
- `supervisor.fail('Personal')` makes GET list/read and PUT return 500 / `unavailable`
- bound without ensure returns 503 / `sync_pending`

Run:

```bash
./olib/scripts/orunr py test services/obsidian/obsidian_vault/tests/test_api.py
```

Expected: at least the new partial-read concurrency contract fails while app/reconcile hold `store.lock_for` across `ensure_vault`.

Then update `app.py` and `reconcile.py` to call the now internally synchronized `supervisor.ensure_vault` without wrapping the one-shot call in `store.lock_for`. Keep the short release/reacquire check lock around `stop_vault`; file reads intentionally do not take it. Replace the old API fake concurrency test with an assertion that concurrent ensure HTTP requests produce one supervisor start via the supervisor state contract.

- [ ] **Step 5: Run all vault-service tests and verify GREEN**

```bash
./olib/scripts/orunr py test services/obsidian/obsidian_vault/tests/
```

Expected: all vault-service unit/API/reconcile tests pass; list/read work against partial checkout, writes remain gated, and no first-sync subprocess wait owns the file lock.

- [ ] **Step 6: Commit and synchronize the vault-service chunk**

```bash
git add services/obsidian/obsidian_vault/files.py \
  services/obsidian/obsidian_vault/main.py \
  services/obsidian/obsidian_vault/app.py \
  services/obsidian/obsidian_vault/reconcile.py \
  services/obsidian/obsidian_vault/tests/test_files.py \
  services/obsidian/obsidian_vault/tests/test_api.py \
  services/obsidian/obsidian_vault/tests/test_reconcile.py
git commit -m "feat(obsidian): allow reads during first sync"
git fetch origin main
git rebase origin/main
git push
```

Stop on rebase conflicts.

---

### Task 3: Mirror the gate in the mock and remove Chief tool retries

**Files:**
- Modify: `backend/libs/clients/obsidian/mock.py`
- Modify: `backend/libs/clients/obsidian/tests/test_mock.py`
- Modify: `backend/libs/tools/tools/obsidian.py`
- Modify: `backend/libs/tools/tests/test_obsidian_tool.py`

- [ ] **Step 1: Write failing mock-client behavior tests**

Replace the old “all file ops stall” test with explicit read/write cases:

```python
def test_read_and_list_work_before_ready_after_ensure(self) -> None:
    client = MockObsidianVaultClient(agent_id='agent-1')
    client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
    client.seed_file('Personal', 'Journal/a.md', 'partial')
    client.set_ready('Personal', False)
    self.assertEqual(client.read_text(vault_id='Personal', path='Journal/a.md'), 'partial')
    self.assertEqual(client.list_dir(vault_id='Personal', path='Journal'), ['a.md'])

def test_write_and_append_stay_pending_before_ready(self) -> None:
    client = MockObsidianVaultClient(agent_id='agent-1')
    client.ensure_vaults([{'vault_id': 'Personal', 'roots': ['Journal']}])
    with self.assertRaises(ObsidianSyncPendingError):
        client.write_text(vault_id='Personal', path='Journal/a.md', content='blocked')
    with self.assertRaises(ObsidianSyncPendingError):
        client.append_text(vault_id='Personal', path='Journal/a.md', content='blocked')
```

Keep root checks active for both read and write operations.

- [ ] **Step 2: Run mock tests and verify RED**

```bash
./olib/scripts/orunr py test backend/libs/clients/obsidian/tests/test_mock.py
```

Expected: read/list raise `ObsidianSyncPendingError` because the mock has one shared `_gate`.

- [ ] **Step 3: Split mock read/write gates**

Refactor `_gate` into a root/binding gate plus a write-ready gate. `list_dir` and `read_text` require a seeded/ensured vault and valid root but not `ready`; `write_text` and `append_text` additionally require `record.ready`. Preserve unseeded/not-found and outside-root behavior.

- [ ] **Step 4: Write failing one-call tool tests**

Replace retry-schedule tests with a parameterized/table-driven test covering all four file operations and both `ObsidianSyncPendingError` and `ObsidianUnavailableError`. For each invocation assert:

```python
self.assertFalse(result['ok'])
self.assertEqual(result['error']['kind'], expected_kind)
client_method.assert_called_once()
```

Remove constructor injection of `sleep` and `delays` from these tests. Keep the status one-call tests, updated for the no-argument `ObsidianTool()` constructor.

- [ ] **Step 5: Run tool tests and verify RED**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py
```

Expected: each retryable file failure makes multiple calls under the current `_call_with_retry`.

- [ ] **Step 6: Remove retry machinery and dispatch exactly once**

In `backend/libs/tools/tools/obsidian.py`:

- remove `time`, `_DEFAULT_RETRY_DELAYS`, `_call_with_retry`, `sleep`/`delays` constructor state, and stale retry comments/docstrings
- call each client method directly in `_dispatch`
- preserve `_failure` mappings and success payloads unchanged

The class should use the default no-argument constructor inherited from `Tool`; do not retain unused compatibility parameters.

- [ ] **Step 7: Run backend Obsidian tests and verify GREEN**

```bash
./olib/scripts/orunr py test \
  backend/libs/clients/obsidian/tests/ \
  backend/libs/tools/tests/test_obsidian_tool.py
```

Expected: all backend Obsidian client/tool tests pass and every file/status failure is returned after one client call.

- [ ] **Step 8: Commit and synchronize the Chief client/tool chunk**

```bash
git add backend/libs/clients/obsidian/mock.py \
  backend/libs/clients/obsidian/tests/test_mock.py \
  backend/libs/tools/tools/obsidian.py \
  backend/libs/tools/tests/test_obsidian_tool.py
git commit -m "feat(obsidian): return file failures without retry"
git fetch origin main
git rebase origin/main
git push
```

Stop on rebase conflicts.

---

### Task 4: Update operator and architecture documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/docs/agents.md`

- [ ] **Step 1: Update the operator contract**

In `docs/docs/agents.md`, replace the blanket first-sync stall description with:

- `list`/`read` may inspect available partial data after first sync starts
- missing partial files still return `not_found`
- `write`/`append` return `sync_pending` immediately until first full Sync
- no file operation retries `sync_pending` or `unavailable`
- use trigger `blocks: [{kind: tool_ready, tool: <id>}]` to prevent a session from starting before first full Sync

- [ ] **Step 2: Update stable architecture**

In the Obsidian vault-service section of `docs/ARCHITECTURE.md`, document the two gates and short-lock invariant: reads require started/non-failed state and may be partial; writes require `.sync-ready`; one-shot subprocess waits do not hold the file lock.

- [ ] **Step 3: Run focused doc/code checks**

```bash
rg "stall|retry|sync_pending|partial" docs/ARCHITECTURE.md docs/docs/agents.md \
  backend/libs/tools/tools/obsidian.py services/obsidian/obsidian_vault/files.py
./olib/scripts/orunr py lint \
  services/obsidian/obsidian_vault \
  backend/libs/clients/obsidian \
  backend/libs/tools/tools/obsidian.py
```

Expected: no stale claim that all file ops stall until ready; lint exits 0.

- [ ] **Step 4: Commit and synchronize docs**

```bash
git add docs/ARCHITECTURE.md docs/docs/agents.md
git commit -m "docs(obsidian): explain partial reads and ready writes"
git fetch origin main
git rebase origin/main
git push
```

Stop on rebase conflicts.

---

## S_final — Code review (mandatory)

### Task 5: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against this plan and the design. Write findings to `2026-08-30-obsidian-read-during-first-sync-review.md`. Under `/ship`, fix or explicitly reject every actionable finding and re-verify before opening the PR.

**Files:**
- Create: `docs/specs/2026-08-30-obsidian-read-during-first-sync/2026-08-30-obsidian-read-during-first-sync-review.md`
- Review: all feature-branch changes against design and plan

- [ ] **Step 1: Run the full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [ ] **Step 2: Get the review range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Dispatch final review**

Read `superpowers/requesting-code-review` and dispatch its reviewer with:

- description: partial Obsidian reads, ready-only writes, explicit supervisor state, and one-call tool failures
- requirements: this design and plan
- range: `$BASE_SHA..$HEAD_SHA`

- [ ] **Step 4: Write and resolve the review artifact**

Write the review file from `requesting-code-review/review-file-template.md`. Under `/ship`, set each finding to `Fixed` after a tested fix or `Rejected` with a technical rationale. If a Critical/Important finding is fixed, run one more full review pass. Stop only when no open actionable rows remain.

- [ ] **Step 5: Re-run verification after review fixes**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0 with all review rows `Fixed` or `Rejected`.

---

## Out of scope

- Detecting live Obsidian Sync catch-up/idle state
- Reading before an ensure attempt starts
- Writing before first full Sync
- Backgrounding the ensure HTTP request
- New vault HTTP endpoints or error kinds
- Trigger-block implementation beyond preserving its `ready` contract
