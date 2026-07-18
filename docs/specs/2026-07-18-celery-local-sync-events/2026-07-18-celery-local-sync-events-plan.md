# Celery Local Sync and Resource Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. `/impl` creates or checks out the declared feature branch before the first code change. Then create `docs/specs/2026-07-18-celery-local-sync-events/2026-07-18-celery-local-sync-events-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Reconcile local agent and credential files through a finite leased Celery Beat task and refresh authenticated users' agent/key lists through user-scoped Redis events, SSE, and htmx partials.

**Architecture:** A focused `apps.local_sync` app owns cross-domain keys-before-agents reconciliation; Celery Beat invokes it every five seconds under an atomic Redis lease. Agent and key command services publish non-authoritative resource hints through foundational `apps.bus` after successful commits, while `apps.web` authenticates one user-scoped SSE stream and serves Postgres-backed htmx partials.

**Tech Stack:** Django 5, Celery Beat, Redis sync/async clients, Django transactions, Jinja, htmx, SSE

**Branch:** `feat/2026-07-18-celery-local-sync-events`

---

## Conventions

- Commands run from the repository root with the consumer prefix: `./olib/scripts/orunr …`.
- Use scoped `./olib/scripts/orunr py test <paths>` commands during each red/green loop; gate every completed task with `./olib/scripts/orunr py test-all`.
- **Git:** this plan/status commit is on `main`; implementation tasks use `feat/2026-07-18-celery-local-sync-events`. After every task commit run `git fetch origin main && git rebase origin/main && git push`. Stop and ask the human if rebase conflicts.
- **TDD:** add the named failing tests first, observe the specified failure, then write only enough production code to pass. One commit per coherent task, not per red/green micro-step.
- **Function documentation:** per `AGENTS.md`, add a brief purpose/assumptions docstring to every function or method written or materially changed. Add comments for the lease ownership invariant, transaction timing, and other non-local behavior.
- **No compatibility re-exports:** use canonical imports, update every call site, and delete replaced watcher/bootstrap files; do not leave import shims.
- **Test bases:** use `OTestCase`, `OTransactionTestCase`, or `OLiveServerTestCase` from `olib.py.django.test.cases`, never bare `unittest.TestCase`.
- **Transactions:** tests asserting `transaction.on_commit` publication use `self.captureOnCommitCallbacks(execute=True)` or `OTransactionTestCase`; events must never publish before rollback is impossible.
- **Test naming:** avoid the parproc log-highlight words listed in `AGENTS.md` (`error`, `exception`, `warning`, `notice`, `deprecated`, `deprecation`) in test names.
- **Views:** authentication/parsing/rendering only; all ORM reads stay in existing query services and all writes stay in domain command services.
- **Secrets:** resource messages contain only `channel` and `resource`; never serialize credential values, agent specs, row ids, usernames, or ownership metadata.
- **Final task:** code review via **`superpowers/requesting-code-review`** is mandatory in **S_final**.

## File and responsibility map

- `backend/apps/bus/resources.py` — canonical user resource channel/envelope/publisher.
- `backend/apps/bus/leases.py` — token-owned Redis lease acquisition and atomic compare-and-delete release.
- `backend/apps/local_sync/apps.py` — Django app declaration only.
- `backend/apps/local_sync/reconcile.py` — configured-root resolution and keys-before-agents finite reconciliation.
- `backend/apps/local_sync/tasks.py` — leased Celery task entry point and failure containment.
- `backend/libs/file/sync.py` — Django-free mutation-aware sync result/report value objects shared by agent and key providers.
- `backend/apps/agents/ingest.py`, `backend/apps/agents/services/config_commands.py`, `backend/apps/agents/services/disk_sync.py`, `backend/apps/agents/delete.py` — mutation detection and committed `agents` hints.
- `backend/apps/keys/services/commands.py`, `backend/apps/keys/services/disk_sync.py` — mutation detection and committed `keys` hints.
- `backend/apps/web/resource_events.py` — authenticated user-resource SSE transport.
- `backend/apps/web/views.py`, `backend/apps/web/urls.py` — query-backed partial endpoints and URL wiring.
- `backend/templates/web/base.html` — one authenticated-page `EventSource` and resource-to-htmx trigger bridge.
- `backend/templates/web/partials/agent_list.html`, `backend/templates/web/partials/key_list.html` — refreshable list-only fragments.
- `backend/templates/web/dashboard.html`, `backend/templates/web/keys.html` — stable page shells; forms and unrelated state remain outside refresh targets.
- `backend/chief/celery.py`, `backend/chief/tasks.py`, `backend/chief/settings.py` — five-second schedule, task registration, and removal of watcher-only settings.
- `backend/apps/web/apps.py`, `backend/apps/runner/apps.py` — inert app startup with no local filesystem or ORM work.
- `backend/apps/web/local_bootstrap.py`, `backend/apps/web/tests/test_local_bootstrap.py` — delete after equivalent Celery coverage exists.
- `docs/ARCHITECTURE.md`, `.env.local.example` — canonical app dependencies and operator lifecycle.

### Task 1: Foundational resource bus and Redis lease primitives

**Files:**
- Create: `backend/apps/bus/resources.py`
- Create: `backend/apps/bus/leases.py`
- Create: `backend/apps/bus/tests/test_resources.py`
- Create: `backend/apps/bus/tests/test_leases.py`

- [ ] **Step 1: Write failing resource bus tests**

Create `test_resources.py` with `OTestCase` coverage that patches `apps.bus.resources.sync_client`, calls the public helpers, and asserts the exact channel and secret-free envelope:

```python
class TestResourceChannels(OTestCase):
    @patch('apps.bus.resources.sync_client')
    def test_publish_uses_user_scoped_channel_and_refresh_hint(self, mock_sync: MagicMock) -> None:
        """Publish only a resource name on the selected user's channel."""
        publish_resource_update(42, 'agents')

        mock_sync.return_value.publish.assert_called_once_with(
            'test:user:42:resources',
            '{"channel": "resource_update", "resource": "agents"}',
        )

    def test_resource_name_rejects_unknown_values(self) -> None:
        """Reject resource names outside the public agents/keys contract."""
        with self.assertRaises(ValueError):
            resource_message(cast(Any, 'credentials'))
```

Use `@override_settings(CACHE_PREFIX='test:')`. Also assert `user_resource_channel(7) == 'test:user:7:resources'` and `resource_message('keys') == {'channel': 'resource_update', 'resource': 'keys'}`.

- [ ] **Step 2: Run the resource tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_resources.py
```

Expected: FAIL because `apps.bus.resources` does not exist.

- [ ] **Step 3: Implement the resource channel contract**

Create `resources.py` with this public API:

```python
ResourceName = Literal['agents', 'keys']
RESOURCE_NAMES: frozenset[str] = frozenset({'agents', 'keys'})

def user_resource_channel(user_id: int) -> str:
    """Return the Redis pub/sub channel for one user's refresh hints."""
    return f'{key_prefix()}user:{user_id}:resources'

def resource_message(resource: ResourceName) -> dict[str, str]:
    """Build a non-authoritative refresh hint without domain data."""
    if resource not in RESOURCE_NAMES:
        raise ValueError(f'unknown resource: {resource}')
    return {'channel': 'resource_update', 'resource': resource}

def publish_resource_update(user_id: int, resource: ResourceName) -> None:
    """Publish one user-scoped resource refresh hint."""
    sync_client().publish(user_resource_channel(user_id), json.dumps(resource_message(resource)))
```

Keep `apps.bus` domain-free: this module imports only stdlib plus `apps.bus.client`.

- [ ] **Step 4: Write failing lease ownership tests**

Create `test_leases.py` with `OTestCase` and a patched Redis client. Assert:

1. `try_acquire_lease('local-sync', 'owner-a', ttl_seconds=30)` calls `set('test:lease:local-sync', 'owner-a', nx=True, ex=30)` and reflects its boolean result.
2. `release_lease('local-sync', 'owner-a')` executes one Lua compare-and-delete script through `eval(script, 1, key, token)`.
3. A fake `eval` returns `0` and preserves `owner-b` when `owner-a` attempts release.
4. The matching owner returns `1` and removes the key.

The fake must model the ownership check rather than merely assert that `delete` was called.

- [ ] **Step 5: Run the lease tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_leases.py
```

Expected: FAIL because `apps.bus.leases` does not exist.

- [ ] **Step 6: Implement atomic token-owned leases**

Create `leases.py` with:

```python
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

def lease_key(name: str) -> str:
    """Return a cache-prefixed key for a distributed lease."""
    return f'{key_prefix()}lease:{name}'

def try_acquire_lease(name: str, owner_token: str, *, ttl_seconds: int) -> bool:
    """Acquire a bounded lease only when no owner currently holds it."""
    return bool(sync_client().set(lease_key(name), owner_token, nx=True, ex=ttl_seconds))

def release_lease(name: str, owner_token: str) -> bool:
    """Release a lease atomically only when the caller still owns it."""
    return bool(sync_client().eval(_RELEASE_SCRIPT, 1, lease_key(name), owner_token))
```

Do not implement `GET` followed by `DELETE`; that race can delete a replacement owner's lease.

- [ ] **Step 7: Verify and commit the foundational bus task**

Run:

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_resources.py backend/apps/bus/tests/test_leases.py backend/apps/bus/tests/test_channels.py
./olib/scripts/orunr py test-all
git add backend/apps/bus/resources.py backend/apps/bus/leases.py backend/apps/bus/tests/test_resources.py backend/apps/bus/tests/test_leases.py
git commit -m "feat(bus): add user resource events and leases"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Expected: tests exit 0; the commit contains only foundational Redis primitives and tests.

### Task 2: Mutation-aware credential synchronization and committed key events

**Files:**
- Create: `backend/libs/file/sync.py`
- Modify: `backend/apps/keys/services/commands.py`
- Modify: `backend/apps/keys/services/disk_sync.py`
- Modify: `backend/apps/agents/services/disk_sync.py`
- Modify: `backend/apps/keys/tests/test_commands.py`
- Modify: `backend/apps/keys/tests/test_disk_sync.py`

- [ ] **Step 1: Write failing command publication tests**

Extend `test_commands.py` with patched `apps.keys.services.commands.publish_resource_update` tests:

```python
@patch('apps.keys.services.commands.publish_resource_update')
def test_upsert_named_publishes_after_commit(self, publish: MagicMock) -> None:
    """Notify the owner only after a UI credential write commits."""
    user = get_user_model().objects.create_user(username='notify-key-user')
    with self.captureOnCommitCallbacks(execute=True):
        commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')
    publish.assert_called_once_with(user.pk, 'keys')

@patch('apps.keys.services.commands.publish_resource_update')
def test_unchanged_disk_upsert_does_not_publish(self, publish: MagicMock) -> None:
    """Suppress refresh hints when a disk credential is unchanged."""
    user = get_user_model().objects.create_user(username='notify-disk-key-user')
    with self.captureOnCommitCallbacks(execute=True):
        commands.upsert_user_named_from_disk(
            user.pk,
            'disk-key',
            'openai',
            'secret',
            source_path='keys/disk-key.yaml',
            source_rev='sha256:same',
        )
    publish.reset_mock()

    with self.captureOnCommitCallbacks(execute=True):
        metadata, changed = commands.upsert_user_named_from_disk(
            user.pk,
            'disk-key',
            'openai',
            'secret',
            source_path='keys/disk-key.yaml',
            source_rev='sha256:same',
        )

    self.assertEqual(metadata.name, 'disk-key')
    self.assertFalse(changed)
    publish.assert_not_called()
```

Add equivalent concrete tests for disk restore/change and `delete_user_credential`; each operation publishes exactly once and only for the owning user.

- [ ] **Step 2: Run command tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_commands.py
```

Expected: FAIL because key commands do not schedule resource publication and disk upsert does not expose mutation state.

- [ ] **Step 3: Add transaction-aware key command publication**

In `commands.py`, import `publish_resource_update` and add:

```python
def _publish_keys_after_commit(user_id: int) -> None:
    """Schedule a key-list refresh only after the surrounding write commits."""
    transaction.on_commit(lambda: publish_resource_update(user_id, 'keys'))
```

Apply these signatures/semantics:

```python
def upsert_user_named(user_id: int, name: str, type_name: str, secret: str) -> KeyMetadata:
    """Create/replace a database-owned key and notify after commit."""

def upsert_user_named_from_disk(
    user_id: int,
    name: str,
    type_name: str,
    secret: str,
    *,
    source_path: str,
    source_rev: str,
) -> tuple[KeyMetadata, bool]:
    """Return metadata plus whether create, replacement, or restore changed the list."""

def delete_user_credential(user_id: int, name: str) -> None:
    """Delete a user key and notify after commit, preserving KeyNotFoundError."""
```

Keep system credentials out of the user resource channel. Callers of `upsert_user_named_from_disk` must unpack the tuple; do not add an alternate compatibility wrapper.

- [ ] **Step 4: Write failing mutation-aware disk report tests**

Extend `test_disk_sync.py` to assert:

```python
self.assertEqual(report.changed_user_ids, {self.user.pk})
self.assertTrue(report.items[0].changed)
self.assertEqual(report.items[0].user_id, self.user.pk)
```

Cover create, content replacement, restore from disabled, and missing-file disable. Add a second invocation with unchanged content and assert:

```python
self.assertEqual(report.changed_user_ids, set())
self.assertFalse(report.items[0].changed)
```

Patch `apps.keys.services.commands.publish_resource_update` around each scan with `captureOnCommitCallbacks(execute=True)` and assert one event for a changed scan, none for an unchanged scan. For a missing-file scan with two credentials owned by one user, assert one event, proving bulk disable is grouped by user.

- [ ] **Step 5: Run disk tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_disk_sync.py
```

Expected: FAIL because `SyncItemResult`/`SyncReport` do not identify changed users and bulk disable bypasses command-side events.

- [ ] **Step 6: Make reports mutation-aware and group disable events**

Move the existing report value objects from `apps.keys.services.disk_sync` to the neutral canonical module `libs.file.sync` and update both key and agent disk-sync imports immediately (no re-export remains in `apps.keys`):

```python
@dataclass(frozen=True)
class SyncItemResult:
    """Describe one file outcome and whether it changed a user's list."""
    source_path: str
    success: bool
    detail: str = ''
    user_id: int | None = None
    changed: bool = False

@dataclass
class SyncReport:
    """Collect file outcomes, disables, and users with visible mutations."""
    items: list[SyncItemResult] = field(default_factory=list)
    disabled: int = 0
    disabled_user_ids: set[int] = field(default_factory=set)

    @property
    def changed_user_ids(self) -> set[int]:
        """Return all owners whose list-visible state changed."""
        return {
            item.user_id for item in self.items if item.success and item.changed and item.user_id is not None
        } | self.disabled_user_ids
```

`sync_key_path` resolves the owner, unpacks `(metadata, changed)`, and returns `user_id`/`changed`. Change:

```python
def soft_disable_missing_disk_keys(*, present_paths: set[str]) -> set[int]:
    """Disable absent disk keys and return distinct affected owners."""
```

Select `(pk, user_id)` before the bulk update, return an empty set for no matches, update exactly those pks, and schedule one `keys` callback per distinct user id after the update. `sync_keys_dir` sets both `report.disabled = len(disabled_ids)` and `report.disabled_user_ids = disabled_user_ids`.

- [ ] **Step 7: Verify and commit credential events**

Run:

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_commands.py backend/apps/keys/tests/test_disk_sync.py backend/apps/keys/tests/test_queries.py
./olib/scripts/orunr py test-all
git add backend/libs/file/sync.py backend/apps/keys/services/commands.py backend/apps/keys/services/disk_sync.py backend/apps/agents/services/disk_sync.py backend/apps/keys/tests/test_commands.py backend/apps/keys/tests/test_disk_sync.py
git commit -m "feat(keys): publish committed credential changes"
git fetch origin main
git rebase origin/main
git push
```

Expected: tests exit 0; no event contains credential material.

### Task 3: Mutation-aware agent synchronization and committed agent events

**Files:**
- Modify: `backend/apps/agents/ingest.py`
- Modify: `backend/apps/agents/services/config_commands.py`
- Modify: `backend/apps/agents/services/disk_sync.py`
- Modify: `backend/apps/agents/delete.py`
- Modify: `backend/apps/web/config_views.py`
- Modify: `backend/apps/agents/tests/test_ingest.py`
- Modify: `backend/apps/agents/tests/test_config_commands.py`
- Modify: `backend/apps/agents/tests/test_disk_sync.py`
- Modify: `backend/apps/agents/tests/test_delete.py`

- [ ] **Step 1: Write failing agent command event tests**

Add tests patching the publisher at each module's canonical import. Use `captureOnCommitCallbacks(execute=True)` and assert:

- `create_agent_from_spec`/`create_from_yaml` publishes `(user.pk, 'agents')` once.
- `persist_agent_config` on an existing agent publishes once.
- `update_agent_profile` returns `True` and publishes once when name/identifier changes; it returns `False` and publishes nothing for identical values.
- `delete_agent_for_user` captures `user_id` before delete and publishes once after commit.

For the config editor's combined profile/config path, establish a `publish_update: bool = True` keyword on `update_agent_profile`; test `publish_update=False` suppresses its callback so the following `persist_agent_config` remains the one event.

- [ ] **Step 2: Run command tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_ingest.py backend/apps/agents/tests/test_config_commands.py backend/apps/agents/tests/test_delete.py
```

Expected: FAIL because agent mutations do not publish and `update_agent_profile` has neither mutation return state nor publication control.

- [ ] **Step 3: Publish agent command mutations after commit**

In each command module, schedule `transaction.on_commit(lambda: publish_resource_update(user_id, 'agents'))`.

Apply these contracts:

```python
def persist_agent_config(
    agent: Agent,
    spec: AgentConfigSpec,
    *,
    source_rev: str,
    dirty: bool = False,
    raw_yaml: str | None = None,
) -> AgentConfig:
    """Persist and materialize a revision, then notify after commit."""

def update_agent_profile(
    agent: Agent,
    user_id: int,
    *,
    name: str | None = None,
    identifier: str | None = None,
    publish_update: bool = True,
) -> bool:
    """Apply visible profile changes and optionally schedule one refresh hint."""

@transaction.atomic
def delete_agent_for_user(user: AbstractBaseUser, agent_id: UUID) -> None:
    """Delete an owned agent and notify that owner after commit."""
```

`create_agent_from_spec` relies on its nested `persist_agent_config` callback; do not schedule a second create callback. In `backend/apps/web/config_views.py`, change the existing `update_agent_profile` call to `publish_update=False`; the subsequent config persist publishes the single combined save event. Although that file changes here, stage it with this task because it is the only caller needing the explicit coalescing contract.

- [ ] **Step 4: Write failing disk mutation report tests**

Extend `test_disk_sync.py` for create, config change, restore, disable, and unchanged scans. Assert the same report contract introduced in Task 2:

```python
self.assertEqual(report.changed_user_ids, {self.user.pk})
self.assertTrue(report.items[0].changed)
self.assertEqual(report.items[0].user_id, self.user.pk)
```

An unchanged second scan must produce `changed_user_ids == set()`, `changed is False`, and no publisher calls. A removed-file scan with two agents for one owner must disable both while publishing one event for that owner.

- [ ] **Step 5: Run disk tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_disk_sync.py
```

Expected: FAIL because agent disk helpers discard mutation/owner information and bulk disable does not publish.

- [ ] **Step 6: Return exact agent mutation state**

Change:

```python
@transaction.atomic
def _persist_parsed_agent(parsed: AgentDiskFile) -> tuple[int, bool]:
    """Persist one disk agent and return its owner plus visible mutation state."""
```

Rules:

- New agent: `create_agent_from_spec` publishes; return `(owner.pk, True)` without another callback.
- Existing agent with profile/status change only: save fields, schedule one callback, return `True`.
- Existing agent with a new config revision: `persist_agent_config` publishes; do not schedule a duplicate even if profile fields also changed.
- Disabled agent restored with unchanged bytes: restore status/beat, schedule once, return `True`.
- Fully unchanged agent: return `(owner.pk, False)` and publish nothing.

`sync_agent_path` copies the returned owner/change state into `SyncItemResult`. Change:

```python
def soft_disable_missing_disk_agents(*, present_paths: set[str]) -> set[int]:
    """Disable missing disk agents and return distinct affected owners."""
```

Select `(id, user_id)`, perform the bulk status update, retain per-agent Beat trigger synchronization, and schedule one resource event per affected owner. Populate `SyncReport.disabled` and `disabled_user_ids`.

- [ ] **Step 7: Verify and commit agent events**

Run:

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_ingest.py backend/apps/agents/tests/test_config_commands.py backend/apps/agents/tests/test_disk_sync.py backend/apps/agents/tests/test_delete.py backend/apps/web/tests/test_config_views.py
./olib/scripts/orunr py test-all
git add backend/apps/agents/ingest.py backend/apps/agents/services/config_commands.py backend/apps/agents/services/disk_sync.py backend/apps/agents/delete.py backend/apps/agents/tests/test_ingest.py backend/apps/agents/tests/test_config_commands.py backend/apps/agents/tests/test_disk_sync.py backend/apps/agents/tests/test_delete.py backend/apps/web/config_views.py
git commit -m "feat(agents): publish committed agent changes"
git fetch origin main
git rebase origin/main
git push
```

Expected: tests exit 0 and each list-visible operation schedules at most one user event.

### Task 4: Finite leased Celery reconciliation and watcher removal

**Files:**
- Create: `backend/apps/local_sync/__init__.py`
- Create: `backend/apps/local_sync/apps.py`
- Create: `backend/apps/local_sync/reconcile.py`
- Create: `backend/apps/local_sync/tasks.py`
- Create: `backend/apps/local_sync/tests/__init__.py`
- Create: `backend/apps/local_sync/tests/test_reconcile.py`
- Create: `backend/apps/local_sync/tests/test_tasks.py`
- Create: `backend/apps/local_sync/tests/test_startup.py`
- Modify: `backend/chief/settings.py`
- Modify: `backend/chief/celery.py`
- Modify: `backend/chief/tasks.py`
- Modify: `backend/chief/tests/test_celery_config.py`
- Modify: `backend/apps/web/apps.py`
- Modify: `backend/apps/runner/apps.py`
- Delete: `backend/apps/web/local_bootstrap.py`
- Delete: `backend/apps/web/tests/test_local_bootstrap.py`

- [ ] **Step 1: Write failing finite reconciliation tests**

In `test_reconcile.py`, test `resolve_local_root()` and:

```python
@override_settings(CHIEF_LOCAL_DIR='')
def test_unset_root_is_inactive(self) -> None:
    """Skip both domains when local providers are not configured."""

def test_missing_root_is_inactive(self) -> None:
    """Skip both domains when the configured root does not exist."""

def test_reconcile_runs_keys_before_agents(self) -> None:
    """Synchronize credentials before agent configs that may reference them."""
```

Patch `apps.local_sync.reconcile.sync_keys_dir` and `sync_agents_dir`; assert exact call order `['keys', 'agents']`, each called with `root=Path(root).resolve()`, and no provider directories are created.

- [ ] **Step 2: Write failing task/Beat/startup tests**

In `test_tasks.py`, patch `try_acquire_lease`, `release_lease`, `reconcile_local_providers`, and `uuid.uuid4`. Assert:

- unconfigured/missing root returns before lease acquisition;
- failed acquisition returns without reconciliation or release;
- acquired lease uses name `local-provider-sync`, token string, TTL `30`, runs reconciliation once, and releases in `finally`;
- a raised reconciliation failure is logged, not retried, and still releases;
- the task is finite: one invocation performs at most one reconciliation call and contains no polling loop/thread.

Extend `test_celery_config.py` to assert:

```python
schedule = app.conf.beat_schedule['local-sync-reconcile']
self.assertEqual(schedule['task'], 'apps.local_sync.tasks.reconcile_local_providers')
self.assertEqual(schedule['schedule'], 5.0)
```

In `test_startup.py`, patch `apps.local_sync.reconcile.sync_keys_dir` and `sync_agents_dir`, instantiate `WebConfig('apps.web', import_module('apps.web'))` and `RunnerConfig('apps.runner', import_module('apps.runner'))`, call `ready()` on both, and assert both sync mocks are untouched. Assert neither config class defines `ready` in its own `__dict__`; this proves they inherit Django's no-op hook and cannot register signals or threads.

- [ ] **Step 3: Run the new tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/local_sync/tests/ backend/chief/tests/test_celery_config.py
```

Expected: FAIL because `apps.local_sync` and the Beat schedule do not exist.

- [ ] **Step 4: Implement the focused local-sync app**

`apps.py`:

```python
class LocalSyncConfig(AppConfig):
    """Declare the cross-domain local provider reconciliation app."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.local_sync'
```

`reconcile.py`:

```python
@dataclass(frozen=True)
class LocalSyncReport:
    """Return separate domain reports while preserving execution order."""
    keys: SyncReport
    agents: SyncReport

def resolve_local_root() -> Path | None:
    """Resolve CHIEF_LOCAL_DIR without creating operator-owned paths."""

def reconcile_local_providers(*, root: Path) -> LocalSyncReport:
    """Run one finite keys-before-agents reconciliation."""
    key_report = sync_keys_dir(root=root)
    agent_report = sync_agents_dir(root=root)
    return LocalSyncReport(keys=key_report, agents=agent_report)
```

`tasks.py`:

```python
LOCAL_SYNC_LEASE_NAME = 'local-provider-sync'
LOCAL_SYNC_LEASE_TTL_SECONDS = 30

@shared_task(ignore_result=True)
def reconcile_local_providers_task() -> None:
    """Run one leased local-provider scan and rely on the next Beat tick after failure."""
```

Set the Celery task's explicit name to `apps.local_sync.tasks.reconcile_local_providers` while keeping the Python function name unambiguous, for example:

```python
@shared_task(name='apps.local_sync.tasks.reconcile_local_providers', ignore_result=True)
def reconcile_local_providers_task() -> None:
```

Resolve and validate the directory before lease acquisition. Generate `owner_token = str(uuid.uuid4())`; return if acquisition fails. In `try`, call the reconciliation function exactly once; catch broad failures only at this task boundary with `logger.exception('Local provider reconciliation failed')`; always release in `finally`. Do not call `retry()`.

- [ ] **Step 5: Register schedule/app and remove process startup behavior**

Add `'apps.local_sync'` to `INSTALLED_APPS`, import `apps.local_sync.tasks` in `chief/tasks.py`, and add:

```python
'local-sync-reconcile': {
    'task': 'apps.local_sync.tasks.reconcile_local_providers',
    'schedule': 5.0,
},
```

Delete `CHIEF_LOCAL_WATCH` from `chief/settings.py`. Reduce `WebConfig` and `RunnerConfig` to declarations with no `ready()` override. Delete `local_bootstrap.py` and its watcher/bootstrap tests after the new task tests pass. Search and update all imports so no canonical code references the deleted module.

- [ ] **Step 6: Verify deletion and commit the Celery replacement**

Run:

```bash
./olib/scripts/orunr py test backend/apps/local_sync/tests/ backend/chief/tests/test_celery_config.py backend/apps/agents/tests/test_disk_sync.py backend/apps/keys/tests/test_disk_sync.py
./olib/scripts/orunr py lint backend/apps/local_sync/ backend/apps/web/apps.py backend/apps/runner/apps.py backend/chief/
./olib/scripts/orunr py mypy backend/apps/local_sync/ backend/chief/
./olib/scripts/orunr py test-all
git add backend/apps/local_sync backend/chief/settings.py backend/chief/celery.py backend/chief/tasks.py backend/chief/tests/test_celery_config.py backend/apps/web/apps.py backend/apps/runner/apps.py
git add -u backend/apps/web/local_bootstrap.py backend/apps/web/tests/test_local_bootstrap.py
git commit -m "feat(local-sync): reconcile providers through Celery Beat"
git fetch origin main
git rebase origin/main
git push
```

Expected: tests exit 0; `rg "CHIEF_LOCAL_WATCH|local_bootstrap|PollingWatcher" backend` returns no matches; no web/management startup path performs local sync.

### Task 5: Authenticated user resource SSE and browser event bridge

**Files:**
- Create: `backend/apps/web/resource_events.py`
- Create: `backend/apps/web/tests/test_resource_events.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/templates/web/base.html`

- [ ] **Step 1: Write failing endpoint authentication/isolation tests**

Create `test_resource_events.py` using `OTransactionTestCase`, `AsyncClient`, and a deterministic fake async Redis client/pubsub. Cover:

- anonymous `GET /events/` redirects to `/admin/login/?next=/events/`;
- an authenticated response is `text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`;
- subscription is exactly `user_resource_channel(authenticated_user.pk)`, never a query-string/user-supplied id;
- one Redis payload `{"channel":"resource_update","resource":"agents"}` yields `event: resource_update` and the same JSON data;
- generator close invokes `unsubscribe(channel)`, `pubsub.close()`, and `client.close()`;
- malformed JSON or an unknown channel/resource is safely skipped/logged without leaking raw data.

Consume one chunk with `await anext(response.streaming_content)` and then call `await response.streaming_content.aclose()` so cleanup is deterministic rather than waiting on an infinite stream.

- [ ] **Step 2: Run endpoint tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py
```

Expected: FAIL because `/events/` and `apps.web.resource_events` do not exist.

- [ ] **Step 3: Implement authenticated resource SSE**

Create:

```python
@require_GET
@login_required(login_url='/admin/login/')
async def resource_events_sse(request: HttpRequest) -> StreamingHttpResponse:
    """Tail only the authenticated user's resource refresh channel."""
```

Resolve `user_id` with `await sync_to_async(_require_authenticated_user_id)(request)`; do not accept identity in route/query/body. The nested `AsyncIterator[str]` subscribes to `user_resource_channel(user_id)`, calls `get_message(ignore_subscribe_messages=True, timeout=1.0)`, validates `channel == 'resource_update'` and `resource in RESOURCE_NAMES`, then yields:

```python
f'event: resource_update\ndata: {json.dumps(raw)}\n\n'
```

Use nested `try/finally` to unsubscribe and close pubsub/client on disconnect. Treat missing Redis configuration like existing session SSE (end stream without breaking page rendering). Add `path('events/', resource_events.resource_events_sse, name='resource_events_sse')`.

- [ ] **Step 4: Write failing base-template bridge test**

Add a template response assertion to `test_resource_events.py`: an authenticated dashboard contains one `new EventSource("/events/")`, registers `resource_update`, maps `agents`/`keys` to `chief:agents-changed`/`chief:keys-changed`, invokes `htmx.trigger(document.body, eventName)`, and closes on `pagehide`. An anonymous dashboard must not contain the `EventSource`.

- [ ] **Step 5: Run the template test and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py
```

Expected: endpoint tests pass but the authenticated base template lacks the browser bridge.

- [ ] **Step 6: Add one authenticated-page EventSource**

Inside `{% if user.is_authenticated %}` in `base.html`, add a single script:

```javascript
const resourceEvents = new EventSource("{{ url('resource_events_sse') }}");
resourceEvents.addEventListener('resource_update', (event) => {
  const message = JSON.parse(event.data);
  const eventName = {
    agents: 'chief:agents-changed',
    keys: 'chief:keys-changed',
  }[message.resource];
  if (message.channel === 'resource_update' && eventName) {
    htmx.trigger(document.body, eventName);
  }
});
window.addEventListener('pagehide', () => resourceEvents.close(), { once: true });
```

Wrap JSON parsing in `try/catch` and ignore invalid hints without logging payloads. Native `EventSource` provides reconnect behavior.

- [ ] **Step 7: Verify and commit SSE transport**

Run:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py backend/apps/web/tests/test_sse.py
./olib/scripts/orunr py test-all
git add backend/apps/web/resource_events.py backend/apps/web/tests/test_resource_events.py backend/apps/web/urls.py backend/templates/web/base.html
git commit -m "feat(web): stream user resource refresh events"
git fetch origin main
git rebase origin/main
git push
```

Expected: tests exit 0 and the new endpoint has no user selector.

### Task 6: Query-backed htmx agent/key partial refreshes

**Files:**
- Create: `backend/templates/web/partials/agent_list.html`
- Create: `backend/templates/web/partials/key_list.html`
- Create: `backend/apps/web/tests/test_resource_partials.py`
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/templates/web/dashboard.html`
- Modify: `backend/templates/web/keys.html`

- [ ] **Step 1: Write failing partial endpoint tests**

Create `test_resource_partials.py` with `OTransactionTestCase`; in `setUp`, create `self.user`, `self.other`, and `self.client = Client()`. Add these concrete cases:

```python
def test_agent_partial_requires_login(self) -> None:
    """Redirect anonymous partial requests to login."""
    response = self.client.get(reverse('dashboard_agents_partial'))
    self.assertRedirects(
        response,
        '/admin/login/?next=/partials/agents/',
        fetch_redirect_response=False,
    )

def test_agent_partial_lists_only_owned_agents(self) -> None:
    """Render only agents owned by the authenticated user."""
    create_from_example(self.user, 'minimal', name='Mine', identifier='mine')
    create_from_example(self.other, 'minimal', name='Theirs', identifier='theirs')
    self.client.force_login(self.user)
    response = self.client.get(reverse('dashboard_agents_partial'))
    self.assertContains(response, 'Mine')
    self.assertNotContains(response, 'Theirs')
    self.assertNotContains(response, 'Recent sessions')

def test_key_partial_requires_login(self) -> None:
    """Redirect anonymous key-list requests to login."""
    response = self.client.get(reverse('settings_keys_partial'))
    self.assertRedirects(
        response,
        '/admin/login/?next=/partials/keys/',
        fetch_redirect_response=False,
    )

def test_key_partial_lists_only_owned_metadata_without_secrets(self) -> None:
    """Render owned key metadata without forms or plaintext values."""
    key_commands.upsert_user_named(self.user.pk, 'mine-key', 'openai', 'mine-secret')
    key_commands.upsert_user_named(self.other.pk, 'their-key', 'openai', 'their-secret')
    self.client.force_login(self.user)
    response = self.client.get(reverse('settings_keys_partial'))
    self.assertContains(response, 'mine-key')
    self.assertNotContains(response, 'their-key')
    self.assertNotContains(response, 'mine-secret')
    self.assertNotContains(response, 'their-secret')
    self.assertNotContains(response, 'name="secret"')
```

Assert `reverse('dashboard_agents_partial') == '/partials/agents/'` and `reverse('settings_keys_partial') == '/partials/keys/'`. The agent response includes the current user's agent and excludes another user's agent and recent-session markup. The key response includes only the current user's key name, excludes another user's key and both plaintext secrets, and has no add form (`name="secret"` absent).

- [ ] **Step 2: Run endpoint tests and verify red**

Run:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_partials.py
```

Expected: FAIL because routes/templates do not exist.

- [ ] **Step 3: Implement authenticated query-backed partial views**

Add to `views.py`:

```python
@login_required(login_url='/admin/login/')
@require_GET
def dashboard_agents_partial(request: HttpRequest) -> HttpResponse:
    """Render the authenticated user's current agent-list fragment."""
    user_id = _require_authenticated_user_id(request)
    data = get_dashboard_data(user_id=user_id)
    return render(request, 'web/partials/agent_list.html', {'agents': data.agents, 'examples': data.examples})

@login_required(login_url='/admin/login/')
@require_GET
def settings_keys_partial(request: HttpRequest) -> HttpResponse:
    """Render credential metadata for the authenticated user's key-list fragment."""
    user_id = _require_authenticated_user_id(request)
    return render(
        request,
        'web/partials/key_list.html',
        {'named_keys': list_user_credentials(user_id)},
    )
```

Register the two exact routes. Views must not import models or issue ORM queries; they reuse existing `apps.web.services.queries.get_dashboard_data` and `apps.keys.services.queries.list_user_credentials`.

- [ ] **Step 4: Extract list-only templates and add htmx containers**

Move the existing dashboard Agents section body into `partials/agent_list.html`. Keep the stable shell:

```html
<section
  id="agent-list"
  class="card"
  hx-get="{{ url('dashboard_agents_partial') }}"
  hx-trigger="chief:agents-changed from:body"
  hx-swap="innerHTML"
>
  {% include "web/partials/agent_list.html" %}
</section>
```

The partial contains the section header/create links/table/empty state, but not the outer `section`, Usage, or Recent sessions.

Move only the key table into `partials/key_list.html`. In `keys.html`:

```html
<div
  id="key-list"
  class="card"
  hx-get="{{ url('settings_keys_partial') }}"
  hx-trigger="chief:keys-changed from:body"
  hx-swap="innerHTML"
>
  {% include "web/partials/key_list.html" %}
</div>
```

Place the existing Add key heading, guide JSON, Alpine state, and form in a separate sibling `.card` after `#key-list`. This guarantees a key event cannot replace a typed secret or selected credential type.

- [ ] **Step 5: Add shell/trigger preservation assertions**

Extend `test_resource_partials.py`:

- dashboard response contains `id="agent-list"`, the exact agent trigger and partial URL, plus `Recent sessions`;
- keys response contains `id="key-list"`, the exact key trigger and partial URL;
- `name="secret"` exists in the full keys page but not in the partial;
- the form is outside the key-list element (parse with `html.parser` or assert the closing `</div>` for `#key-list` precedes the Add key heading);
- an htmx partial request still redirects when anonymous and never exposes another user's rows.

- [ ] **Step 6: Verify and commit htmx partials**

Run:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_partials.py backend/apps/web/tests/test_create_agent.py backend/apps/web/tests/test_keys_page.py backend/apps/web/tests/test_usage_views.py backend/apps/web/tests/test_session_dialog.py
./olib/scripts/orunr py test-all
git add backend/apps/web/views.py backend/apps/web/urls.py backend/apps/web/tests/test_resource_partials.py backend/templates/web/dashboard.html backend/templates/web/keys.html backend/templates/web/partials/agent_list.html backend/templates/web/partials/key_list.html
git commit -m "feat(web): refresh agent and key list partials"
git fetch origin main
git rebase origin/main
git push
```

Expected: tests exit 0; htmx swaps only list containers.

### Task 7: Architecture, operator docs, and integrated acceptance gate

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `.env.local.example`
- Test: all files changed in Tasks 1–6

- [ ] **Step 1: Update canonical architecture boundaries**

In `docs/ARCHITECTURE.md`:

1. Add `apps.local_sync` to the app-role table as finite cross-domain local-provider reconciliation.
2. Update dependency rows so `apps.agents` and `apps.keys` may import foundational `apps.bus` publishers, while `apps.bus` remains Django/stdlib-only and imports no domain app.
3. Add `apps.local_sync` dependencies on `agents`, `keys`, and `bus`; no domain app may import `apps.local_sync`.
4. Replace the old web boot/watch paragraph with: Beat enqueues every five seconds, a Redis token lease prevents overlap, keys run before agents, absent configuration is a no-op, and Postgres remains runtime source of truth.
5. Extend Real-time UI notifications with `{CACHE_PREFIX}user:{user_id}:resources`, exact `resource_update` envelope, committed command publication, authenticated `/events/`, and refetch-only semantics.
6. State explicitly that `AppConfig.ready()` performs no local-provider filesystem/ORM work.

- [ ] **Step 2: Update operator configuration**

Remove the obsolete `CHIEF_LOCAL_WATCH` block from `.env.local.example`. Keep API key and `CREDENTIALS_KEY` guidance unchanged. Add a concise comment that Compose sets `CHIEF_LOCAL_DIR=/mnt/local` on backend, worker, and Beat, while only Beat/worker task execution initiates reconciliation.

Do not edit the unrelated cloud-file-integration spec.

- [ ] **Step 3: Run targeted architecture/behavior checks**

Run:

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/ backend/apps/local_sync/tests/ backend/apps/agents/tests/test_disk_sync.py backend/apps/keys/tests/test_disk_sync.py backend/apps/web/tests/test_resource_events.py backend/apps/web/tests/test_resource_partials.py
./olib/scripts/orunr py lint backend/apps/bus/ backend/apps/local_sync/ backend/apps/agents/ backend/apps/keys/ backend/apps/web/ backend/chief/
./olib/scripts/orunr py mypy backend/apps/bus/ backend/apps/local_sync/ backend/apps/agents/ backend/apps/keys/ backend/apps/web/ backend/chief/
```

Expected: all commands exit 0.

- [ ] **Step 4: Run the mandatory full quality gate**

Run:

```bash
./olib/scripts/orunr py test-all
```

Expected: lint, mypy, tests, and bandit all exit 0, including existing local-provider, web, and session SSE coverage.

- [ ] **Step 5: Perform acceptance searches**

Run:

```bash
rg "CHIEF_LOCAL_WATCH|local_bootstrap|PollingWatcher|start_watcher|sync_after_migrate" backend .env.local.example docs/ARCHITECTURE.md
rg "resource_update|chief:agents-changed|chief:keys-changed|local-sync-reconcile" backend docs/ARCHITECTURE.md
git diff --check
git status --short
```

Expected: the first search has no matches; the second shows bus, SSE, templates, Beat schedule, tests, and architecture docs; `git diff --check` exits 0; status contains only Task 7 docs before commit.

- [ ] **Step 6: Commit documentation and final gate evidence**

```bash
git add docs/ARCHITECTURE.md .env.local.example
git commit -m "docs: describe Celery local sync events"
git fetch origin main
git rebase origin/main
git push
```

Expected: one docs commit; no unrelated spec files are staged.

---

## S_final — Code review (mandatory)

### Task 8: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

**Files:** review only — no edits unless user requests fixes.

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Read `superpowers/requesting-code-review`. Dispatch the reviewer with:

- `{DESCRIPTION}` — finite leased Celery local reconciliation, mutation-aware resource publication, authenticated user SSE, and htmx list refreshes.
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-18-celery-local-sync-events/2026-07-18-celery-local-sync-events-design.md` and `docs/specs/2026-07-18-celery-local-sync-events/2026-07-18-celery-local-sync-events-plan.md`.
- `{BASE_SHA}` / `{HEAD_SHA}` — values from Step 2.

- [ ] **Step 4: Write review file and report findings**

Read `superpowers/requesting-code-review` and `review-file-template.md`.

1. Write `docs/specs/2026-07-18-celery-local-sync-events/2026-07-18-celery-local-sync-events-review.md`.
2. Use one issue table per severity with columns `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**.
3. Summarize the same assessment and tables in chat.
4. Stop unless the user asks to fix findings.

- [ ] **Step 5: Track feedback**

When the user requests fixes or rejects findings, update **Status** in the review file:

- **Fixed** after implementing and verifying a fix.
- **Rejected** when the user declines it, with rationale in **Notes**.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. When the replacement PR is opened, close GitHub PR #11 as superseded and link the replacement PR; do not close #11 before that replacement exists.

## Out of scope

- Replay storage or authoritative row payloads on the resource channel.
- A long-running filesystem watcher, debounce loop, or per-process ASGI/thread coordinator.
- Refactoring the existing session-scoped SSE protocol.
- Editing the unrelated cloud-file-integration design/spec.
- Closing PR #11 before the replacement implementation PR is open.
