# Obsidian vault `status` tool function — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/ship` first uses `superpowers/using-git-worktrees`, then uses `superpowers/subagent-driven-development` (recommended) or `superpowers/executing-plans` to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-30-obsidian-vault-status/2026-08-30-obsidian-vault-status-revision.md` from the existing revision stub pattern — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** `/ship` owns **S_final** (`superpowers/requesting-code-review`) — do not run finish menus from the executor.

**Goal:** Add `obsidian` tool function `status` (`vault__status`) that returns the vault service first-sync/process-liveness snapshot without stalling.

**Architecture:** Reuse `GET /v1/vaults/{vault_id}/status`. Add `get_status` on the HTTP client, protocol, and mock. Dispatch from `ObsidianTool` with an empty argument object and **no** `_call_with_retry`. Document in `docs/docs/agents.md`.

**Tech Stack:** Python, httpx, Django `OTestCase`, existing vault-service HTTP API (unchanged).

**Branch:** `feat/2026-08-30-obsidian-vault-status` (copy from `-design.md` — must match exactly)

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …` while iterating; `./olib/scripts/orunr py test-all` before S_final
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-30-obsidian-vault-status`. After each stage commit: `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` only — never bare `unittest.TestCase`
- **Test names:** do not use the words error / exception / warning / deprecated (see `AGENTS.md`)
- **Do not add license headers** — pre-commit adds them
- **Final task:** code review via **`superpowers/requesting-code-review`** (see **S_final**; `/ship` auto-fixes findings)
- Vault service HTTP API is **out of scope** — do not change `services/obsidian/`

## File map

| File | Responsibility |
|------|----------------|
| `backend/libs/clients/obsidian/protocol.py` | `get_status` on the protocol |
| `backend/libs/clients/obsidian/client.py` | GET + parse status body |
| `backend/libs/clients/obsidian/mock.py` | In-memory status without the file-op gate |
| `backend/libs/clients/obsidian/tests/test_client.py` | HTTP contract for `get_status` |
| `backend/libs/clients/obsidian/tests/test_mock.py` | Mock status behavior |
| `backend/libs/tools/tools/obsidian.py` | `status` function, no retry |
| `backend/libs/tools/tests/test_obsidian_tool.py` | Schema, dispatch, no-retry |
| `docs/docs/agents.md` | Operator-facing function table |
| `backend/libs/agent_spec/examples/journal-obsidian.yaml` | Add `status` to `allow` |

---

### Task 1: Client, protocol, and mock `get_status`

**Files:**
- Modify: `backend/libs/clients/obsidian/protocol.py`
- Modify: `backend/libs/clients/obsidian/client.py`
- Modify: `backend/libs/clients/obsidian/mock.py`
- Modify: `backend/libs/clients/obsidian/tests/test_client.py`
- Modify: `backend/libs/clients/obsidian/tests/test_mock.py`

- [ ] **Step 1: Write the failing client tests**

Add a class to `test_client.py` (after the lifecycle tests, before file-ops is fine):

```python
class TestObsidianVaultClientStatus(OTestCase):
    def test_get_status_gets_vault_path_and_returns_bool_fields(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['path'] = request.url.path
            captured['auth'] = request.headers.get('Authorization')
            return httpx.Response(
                200,
                json={
                    'vault_id': 'Personal',
                    'ready': True,
                    'initial_sync_complete': True,
                    'sync_process_alive': False,
                },
            )

        body = _client(handler).get_status(vault_id='Personal')

        self.assertEqual(captured['method'], 'GET')
        self.assertEqual(captured['path'], '/v1/vaults/Personal/status')
        self.assertEqual(captured['auth'], 'Bearer service-token')
        self.assertEqual(
            body,
            {
                'vault_id': 'Personal',
                'ready': True,
                'initial_sync_complete': True,
                'sync_process_alive': False,
            },
        )

    def test_get_status_rejects_non_bool_flags(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    'vault_id': 'Personal',
                    'ready': 1,
                    'initial_sync_complete': True,
                    'sync_process_alive': True,
                },
            )

        with self.assertRaises(ObsidianUnavailableError):
            _client(handler).get_status(vault_id='Personal')

    def test_get_status_maps_bare_401_to_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'detail': 'Unauthorized'})

        with self.assertRaises(ObsidianAuthError):
            _client(handler).get_status(vault_id='Personal')
```

Add mock tests in `test_mock.py`:

```python
    def test_get_status_unseeded_vault_is_all_false(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        self.assertEqual(
            client.get_status(vault_id='Unknown'),
            {
                'vault_id': 'Unknown',
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': False,
            },
        )

    def test_get_status_does_not_stall_when_not_ready(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=False)
        self.assertEqual(
            client.get_status(vault_id='Personal'),
            {
                'vault_id': 'Personal',
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': True,
            },
        )

    def test_get_status_can_report_dead_sync_child(self) -> None:
        client = MockObsidianVaultClient(agent_id='agent-1')
        client.seed_vault('Personal', ready=True)
        client.set_sync_process_alive('Personal', False)
        self.assertFalse(client.get_status(vault_id='Personal')['sync_process_alive'])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/libs/clients/obsidian/tests/test_client.py backend/libs/clients/obsidian/tests/test_mock.py
```

Expected: FAIL (`get_status` missing on client/mock).

- [ ] **Step 3: Implement protocol, client parse, and mock**

On `ObsidianVaultClientProtocol`, add (update the class docstring to mention status):

```python
    def get_status(self, *, vault_id: str) -> dict[str, Any]:
        """Return first-sync readiness and continuous-sync process liveness for vault_id."""
```

In `client.py`, add a helper and method. Parse **only** real `bool` (`isinstance(..., bool)`), non-empty `str` vault_id. GET `/v1/vaults/{vault_id}/status`. Return a four-key dict. Raise `ObsidianUnavailableError` on invalid success bodies.

```python
def _parse_status_body(body: dict[str, Any]) -> dict[str, Any]:
    """Require the vault-service status shape; reject truthy non-bools."""
    vault_id = body.get('vault_id')
    ready = body.get('ready')
    initial_sync_complete = body.get('initial_sync_complete')
    sync_process_alive = body.get('sync_process_alive')
    if (
        not isinstance(vault_id, str)
        or not vault_id
        or not isinstance(ready, bool)
        or not isinstance(initial_sync_complete, bool)
        or not isinstance(sync_process_alive, bool)
    ):
        raise ObsidianUnavailableError('obsidian vault service returned invalid status')
    return {
        'vault_id': vault_id,
        'ready': ready,
        'initial_sync_complete': initial_sync_complete,
        'sync_process_alive': sync_process_alive,
    }
```

```python
    def get_status(self, *, vault_id: str) -> dict[str, Any]:
        """Fetch vault-level first-sync and process-liveness flags (`GET /v1/vaults/{vault_id}/status`)."""
        return _parse_status_body(self._request('GET', f'/v1/vaults/{vault_id}/status'))
```

In `mock.py`:

- Add `_sync_alive: dict[str, bool] = field(default_factory=dict)` on the client **or** a simple instance dict in `__init__`.
- `set_sync_process_alive(self, vault_id: str, alive: bool) -> None` with a docstring.
- `get_status`: if vault not in `_vaults`, return all-false with the requested `vault_id`. Else `ready` and `initial_sync_complete` both from `record.ready`; `sync_process_alive` from `_sync_alive.get(vault_id, True)`. **Do not** call `_gate`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/libs/clients/obsidian/tests/test_client.py backend/libs/clients/obsidian/tests/test_mock.py
```

Expected: PASS.

- [ ] **Step 5: Commit and sync (PR-ready chunk)**

```bash
git add backend/libs/clients/obsidian/protocol.py backend/libs/clients/obsidian/client.py backend/libs/clients/obsidian/mock.py backend/libs/clients/obsidian/tests/test_client.py backend/libs/clients/obsidian/tests/test_mock.py
git commit -m "$(cat <<'EOF'
feat(obsidian): add vault client get_status

EOF
)"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 2: `ObsidianTool` `status` function without stall retry

**Files:**
- Modify: `backend/libs/tools/tools/obsidian.py`
- Modify: `backend/libs/tools/tests/test_obsidian_tool.py`

- [ ] **Step 1: Write the failing tool tests**

Update `test_exposes_exact_function_surface` so the set is `{'list', 'read', 'write', 'append', 'status'}` and `functions['status'].readonly` is True.

Update `test_function_schemas_apply_exact_constraints` to include:

```python
                'status': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                    'additionalProperties': False,
                },
```

(If the implementation omits `'required': []`, match whatever the tool actually publishes — prefer including `'required': []` for an explicit empty object.)

Add to `test_dispatches_exact_protocol_arguments`:

```python
        client.get_status.return_value = {
            'vault_id': 'Personal',
            'ready': True,
            'initial_sync_complete': True,
            'sync_process_alive': True,
        }
        ...
        self.assertEqual(
            invoke('status', {}),
            {
                'ok': True,
                'vault_id': 'Personal',
                'ready': True,
                'initial_sync_complete': True,
                'sync_process_alive': True,
            },
        )
        client.get_status.assert_called_once_with(vault_id='Personal')
```

Extend malformed cases with `('status', {'path': 'Journal'}, 'Obsidian tool arguments are invalid')`.

Add:

```python
    def test_status_does_not_retry_sync_pending(self) -> None:
        """Surface sync_pending from status immediately without sleeping."""
        client = MagicMock()
        client.get_status.side_effect = ObsidianSyncPendingError('first sync not complete')
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('status', {})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'sync_pending', 'message': 'first sync not complete'}},
        )
        self.assertEqual(recorded_delays, [])
        self.assertEqual(client.get_status.call_count, 1)

    def test_status_does_not_retry_unavailable(self) -> None:
        """Surface unavailable from status immediately without sleeping."""
        client = MagicMock()
        client.get_status.side_effect = ObsidianUnavailableError('vault service unreachable')
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('status', {})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'unavailable', 'message': 'vault service unreachable'}},
        )
        self.assertEqual(recorded_delays, [])
        self.assertEqual(client.get_status.call_count, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py
```

Expected: FAIL (unknown function / schema set mismatch).

- [ ] **Step 3: Implement `status` dispatch without retry**

In `obsidian.py`:

- `_REQUIRED_ARGUMENTS['status'] = ()`
- `_ARGUMENT_FIELDS['status'] = frozenset()`
- `_valid_arguments`: only validate `path`/`content` when those keys are in `allowed`. Empty `allowed` + empty `required` means `arguments` must be `{}`.
- `_dispatch`: if `function == 'status'`, call `client.get_status(vault_id=config.vault)` **directly** (not `_call_with_retry`) and return `{'ok': True, **body}`.
- `functions()`: append a readonly `ToolFunction` named `status` whose description states this is first-sync readiness and continuous-process liveness, **not** Obsidian Sync “caught up”. Schema: object, `properties: {}`, `required: []`, `additionalProperties: False`.
- Update the module docstring that currently says four file operations.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py backend/libs/clients/obsidian/tests/test_mock.py
```

Expected: PASS.

- [ ] **Step 5: Commit and sync**

```bash
git add backend/libs/tools/tools/obsidian.py backend/libs/tools/tests/test_obsidian_tool.py
git commit -m "$(cat <<'EOF'
feat(obsidian): expose vault status without stall retry

EOF
)"
git fetch origin main
git rebase origin/main
git push
```

---

### Task 3: Operator docs and journal example allow-list

**Files:**
- Modify: `docs/docs/agents.md`
- Modify: `backend/libs/agent_spec/examples/journal-obsidian.yaml`

- [ ] **Step 1: Update docs**

In the `obsidian` tool section of `docs/docs/agents.md`:

- Opening sentence: mention `status` in addition to file ops.
- Function table: add `| status | Report first-sync readiness and whether continuous headless Sync is alive | yes |`
- After the stall paragraph, add: `status` does **not** stall/retry; `ready: false` is a successful observation. Fields are Chief first-sync/process liveness, not `ob sync-status` and not “vault is caught up with Sync”.

In `journal-obsidian.yaml` set `allow: [append, read, list, status]`.

- [ ] **Step 2: Confirm no tests pin the old allow list**

```bash
./olib/scripts/orunr py test backend/libs/tools/tests/test_obsidian_tool.py backend/libs/agent_spec
```

Expected: PASS (skip `agent_spec` path if that package has no tests; do not invent failures).

- [ ] **Step 3: Commit and sync**

```bash
git add docs/docs/agents.md backend/libs/agent_spec/examples/journal-obsidian.yaml
git commit -m "$(cat <<'EOF'
docs(agents): document obsidian status tool function

EOF
)"
git fetch origin main
git rebase origin/main
git push
```

---

## S_final — Code review (mandatory)

### Task 4: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Under `/ship`, the parent owns this task after implementation workers return. Dispatch a code reviewer subagent using `requesting-code-review/code-reviewer.md`. Write findings to **`*-review.md`**. `/ship` then fixes actionable findings before opening a PR.

**Files:** (review only until `/ship` fix pass)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Dispatch reviewer with design + plan paths and BASE/HEAD SHAs.

- [ ] **Step 4: Write review file**

`docs/specs/2026-08-30-obsidian-vault-status/2026-08-30-obsidian-vault-status-review.md`

- [ ] **Step 5: Track feedback** — Status `Fixed` / `Rejected` as `/ship` resolves rows.

---

## Out of scope

- Vault service changes, `ob` subprocess, inferred “caught up” health
- Changing file-op retry delays
- Schema version bump (new optional tool function; explicit `allow` lists are operator config)

## Spec coverage

| Design requirement | Task |
|--------------------|------|
| `get_status` HTTP + parse | 1 |
| Mock without file-op gate | 1 |
| Tool `status`, no retry | 2 |
| agents.md + journal allow | 3 |
| No vault-service edits | (explicit out of scope) |
