# ClickUp library and tool Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **7 of 9** · Item: **ClickUp library and tool**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Ship the ClickUp integration mirroring Gmail's shape — a generic `libs/clients/clickup` REST client (personal token), a `clickup` source adapter that polls a list into a queue, and a gated `clickup` tool — reusing the shared `ToolInstance.config` and `{data, ref}` envelope from spec 6.

**Architecture:** Three Django-free lib components (client / source adapter / tool) wired through the same seams as Gmail: `apps.queues.tasks.poll_source` calls the adapter; `apps.agents.tool_wiring.build_bound_tools` binds the tool with a `token_supplier` + `config`. The client is a thin `httpx` wrapper (no official ClickUp SDK). Secrets resolve lazily per request via `apps.keys.make_secret_supplier` (spec 1).

**Tech Stack:** Django 5.2, Pydantic v2, `httpx`, existing `libs/tools` + `libs/sources` registries, Celery beat poll.

**Branch:** `feat/2026-07-06-service-integrations`

**Design spec:** [`2026-07-06-clickup-integration-design.md`](./2026-07-06-clickup-integration-design.md)
**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

> **DEPENDENCY:** This plan **requires Gmail spec 6 S1** (`ToolInstance.config` + `tool_wiring`
> threading) to be merged first. Both specs share branch `feat/2026-07-06-service-integrations`;
> implement spec 6 S1 before starting here. Do **not** re-add `ToolInstance.config`.

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-06-service-integrations || git checkout -b feat/2026-07-06-service-integrations
git branch --show-current   # must print feat/2026-07-06-service-integrations
```

Confirm spec 6 S1 landed: `git grep -n "config: dict\[str, Any\] = {}" backend/libs/agent_spec/spec.py` prints the `ToolInstance.config` line.

- [ ] **Step 0b: Ensure review template exists**

Create `docs/specs/2026-07-06-clickup-integration/2026-07-06-clickup-integration-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md`. Leave review sections empty; do not read it again during implementation.

- [ ] **Step 0c: Commit plan (if uncommitted)**

```bash
git add docs/specs/2026-07-06-clickup-integration/
git commit -m "docs(clickup): add clickup integration plan"
git fetch origin main && git rebase origin/main && git push
```

Skip 0c if already committed on the feature branch.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped `./olib/scripts/orunr py test backend/<path>` while iterating — see `ai/commands/py-checks.md`)
- Add dependencies by editing `backend/pyproject.toml`, then `./olib/scripts/orun py sync` (mutating `orun`, run on host)
- Test base: `OTestCase` from `olib.py.django.test.cases`
- **Parproc naming:** never use `error`, `exception`, `warning`, `notice`, `deprecated` in test names — use `failure`, `raises`, `invalid`, `bad_request`, `caution`, `legacy`
- **Lib rules (`AGENTS.local.md`):** `libs/*` never import `apps.*`; credentials injected at the app boundary as a `token_supplier`; never store the plaintext token on client state beyond one request
- **Function documentation:** every new/changed function/method gets a brief docstring per [`AGENTS.md`](../../AGENTS.md)
- **No compatibility re-exports:** update imports to the canonical module; delete replaced files
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final below)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts
- **Uniform failure result:** reuse the exact `{ok: false, error: {kind, message}}` shape from the Gmail tool (S4 of spec 6) so both tools behave identically

---

## File structure

```
backend/
  libs/
    clients/
      clickup/
        __init__.py             # NEW — exports ClickUpClient + errors
        client.py               # NEW — ClickUpClient (httpx REST wrapper)
        errors.py               # NEW — ClickUpError hierarchy
        tests/
          __init__.py           # NEW
          test_client.py        # NEW (httpx.MockTransport)
    sources/
      adapters/
        clickup.py              # NEW — ClickUpSourceAdapter (auto-discovered)
      tests/
        test_clickup_adapter.py # NEW
    tools/
      tools/
        clickup.py              # NEW — ClickUpTool
      tests/
        test_clickup_tool.py    # NEW
    agent_specs/
      examples/
        clickup-inbox.yaml      # NEW — illustrative example
  apps/
    agents/
      tools_wiring.py           # MODIFY — register ClickUpTool
      tests/
        test_tool_wiring.py     # MODIFY — clickup wiring round-trip
  pyproject.toml                # MODIFY — add httpx
```

---

## S1 — ClickUp client (`libs/clients/clickup`)

**Files:**
- Create: `backend/libs/clients/clickup/__init__.py`, `client.py`, `errors.py`, `tests/__init__.py`, `tests/test_client.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add dependency**

Add to `backend/pyproject.toml` `dependencies` (pin latest resolvable; example):

```toml
  "httpx==0.28.1",
```

Then:

```bash
./olib/scripts/orun py sync
```

- [ ] **Step 2: Write `errors.py`**

`backend/libs/clients/clickup/errors.py`:

```python
"""Typed ClickUp client failures (mapped to tool/source failure results by callers)."""

from __future__ import annotations


class ClickUpError(Exception):
    """Base class for all ClickUp client failures."""


class ClickUpAuthError(ClickUpError):
    """Missing/invalid token (401/403)."""


class ClickUpNotFoundError(ClickUpError):
    """Referenced task/list/space does not exist (404)."""


class ClickUpAPIError(ClickUpError):
    """Other non-2xx ClickUp response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
```

- [ ] **Step 3: Write the failing client tests**

`backend/libs/clients/clickup/tests/test_client.py` (uses `httpx.MockTransport` — no network):

```python
"""Unit tests for ClickUpClient using an injected httpx MockTransport."""

from __future__ import annotations

import json

import httpx
from libs.clients.clickup.client import ClickUpClient
from libs.clients.clickup.errors import ClickUpАuthError if False else __import__('libs.clients.clickup.errors', fromlist=['ClickUpAuthError']).ClickUpAuthError  # noqa: E501

from olib.py.django.test.cases import OTestCase


def _client(handler, *, token: str | None = 'pk_test') -> ClickUpClient:
    """Build a ClickUpClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    return ClickUpClient(token_supplier=lambda: token, config={'team_id': '9'}, transport=transport)


class TestClickUpClient(OTestCase):
    def test_list_tasks_parses_tasks_and_sends_auth_header(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['auth'] = request.headers.get('Authorization', '')
            captured['url'] = str(request.url)
            return httpx.Response(200, json={'tasks': [{'id': 't1'}, {'id': 't2'}], 'last_page': True})

        client = _client(handler)
        out = client.list_tasks(list_id='901', statuses=('open',))
        self.assertEqual([t['id'] for t in out['tasks']], ['t1', 't2'])
        self.assertEqual(captured['auth'], 'pk_test')
        self.assertIn('/list/901/task', captured['url'])
        self.assertIn('statuses%5B%5D=open', captured['url'])  # statuses[]=open

    def test_create_task_posts_body(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured['method'] = request.method
            captured['body'] = json.loads(request.content)
            return httpx.Response(200, json={'id': 't9', 'name': 'New'})

        client = _client(handler)
        out = client.create_task(list_id='901', name='New', description='d')
        self.assertEqual(out['id'], 't9')
        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['body'], {'name': 'New', 'description': 'd'})

    def test_404_maps_to_not_found(self) -> None:
        from libs.clients.clickup.errors import ClickUpNotFoundError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={'err': 'not found', 'ECODE': 'x'})

        client = _client(handler)
        with self.assertRaises(ClickUpNotFoundError):
            client.get_task('missing')

    def test_401_maps_to_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={'err': 'token invalid'})

        client = _client(handler)
        with self.assertRaises(ClickUpAuthError):
            client.list_teams()

    def test_missing_token_raises_auth_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        client = _client(handler, token=None)
        with self.assertRaises(ClickUpAuthError):
            client.list_teams()
```

> **Note:** delete the obfuscated import line above and use a plain import in the real file:
> `from libs.clients.clickup.errors import ClickUpAuthError` (the inline expression is only to
> make the failing-first import explicit; write the clean import when you create the test).

- [ ] **Step 4: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/clients/clickup/tests/test_client.py -v`
Expected: FAIL — module `libs.clients.clickup.client` does not exist.

- [ ] **Step 5: Write `client.py`**

`backend/libs/clients/clickup/client.py`:

```python
"""Generic ClickUp API v2 client (Django-free) authenticated by a personal token.

No official ClickUp SDK exists, so this is a thin `httpx` wrapper. The token is supplied
lazily by `token_supplier` and read per request; it is never stored on the client beyond a
single call (secret-retention rule, docs/ARCHITECTURE.md). `transport` is a test seam.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from libs.clients.clickup.errors import (
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpNotFoundError,
)

_DEFAULT_BASE_URL = 'https://api.clickup.com/api/v2'
_TIMEOUT = 30.0


class ClickUpClient:
    """Thin wrapper over the ClickUp v2 REST API."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token_supplier = token_supplier
        self._config = config or {}
        self._base_url = self._config.get('base_url', _DEFAULT_BASE_URL)
        self._transport = transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue one request; map non-2xx to typed ClickUp failures; return parsed JSON."""
        token = self._token_supplier()
        if not token:
            raise ClickUpAuthError('no clickup credential resolved')
        headers = {'Authorization': token}
        with httpx.Client(base_url=self._base_url, transport=self._transport, timeout=_TIMEOUT) as client:
            resp = client.request(method, path, params=params, json=json_body, headers=headers)
        if resp.status_code in (401, 403):
            raise ClickUpAuthError(f'clickup auth failed ({resp.status_code})')
        if resp.status_code == 404:
            raise ClickUpNotFoundError(f'clickup resource not found: {path}')
        if resp.status_code >= 400:
            raise ClickUpAPIError(f'clickup api failure ({resp.status_code})', status=resp.status_code)
        return resp.json()

    def list_teams(self) -> dict[str, Any]:
        """List workspaces (teams) the token can access."""
        return self._request('GET', '/team')

    def list_spaces(self, team_id: str) -> dict[str, Any]:
        """List spaces in a workspace."""
        return self._request('GET', f'/team/{team_id}/space')

    def list_lists(self, space_id: str) -> dict[str, Any]:
        """List folderless lists in a space."""
        return self._request('GET', f'/space/{space_id}/list')

    def list_tasks(
        self,
        *,
        list_id: str,
        statuses: tuple[str, ...] = (),
        updated_gt: int | None = None,
        include_closed: bool = False,
        page: int = 0,
    ) -> dict[str, Any]:
        """List tasks in a list, optionally filtered by status/update time."""
        params: dict[str, Any] = {'page': page, 'include_closed': str(include_closed).lower()}
        if statuses:
            params['statuses[]'] = list(statuses)
        if updated_gt is not None:
            params['date_updated_gt'] = updated_gt
        return self._request('GET', f'/list/{list_id}/task', params=params)

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch one task."""
        return self._request('GET', f'/task/{task_id}')

    def create_task(
        self, *, list_id: str, name: str, description: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """Create a task in a list (used for INBOX routing)."""
        body: dict[str, Any] = {'name': name}
        if description is not None:
            body['description'] = description
        if status is not None:
            body['status'] = status
        return self._request('POST', f'/list/{list_id}/task', json_body=body)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Update task fields (name/status/description/…)."""
        return self._request('PUT', f'/task/{task_id}', json_body=dict(fields))

    def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
        """Add a comment to a task."""
        return self._request('POST', f'/task/{task_id}/comment', json_body={'comment_text': text})

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Delete a task (denied by default in example configs)."""
        return self._request('DELETE', f'/task/{task_id}')
```

- [ ] **Step 6: Write `__init__.py` files**

`backend/libs/clients/clickup/__init__.py`:

```python
"""ClickUp API client package."""

from libs.clients.clickup.client import ClickUpClient
from libs.clients.clickup.errors import (
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpError,
    ClickUpNotFoundError,
)

__all__ = [
    'ClickUpAPIError',
    'ClickUpAuthError',
    'ClickUpClient',
    'ClickUpError',
    'ClickUpNotFoundError',
]
```

`backend/libs/clients/clickup/tests/__init__.py`: empty file. (`backend/libs/clients/__init__.py` already exists from spec 6 S2.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/clients/clickup/tests/test_client.py -v`
Expected: PASS

- [ ] **Step 8: Commit and sync**

```bash
git add backend/libs/clients/clickup/ backend/pyproject.toml backend/uv.lock
git commit -m "feat(clients): add generic ClickUp REST client"
git fetch origin main && git rebase origin/main && git push
```

---

## S2 — ClickUp source adapter

**Files:**
- Create: `backend/libs/sources/adapters/clickup.py`
- Create: `backend/libs/sources/tests/test_clickup_adapter.py`

- [ ] **Step 1: Write the failing adapter tests**

`backend/libs/sources/tests/test_clickup_adapter.py`:

```python
"""Tests for the ClickUp source adapter (client stubbed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter

from olib.py.django.test.cases import OTestCase


class _FakeClickUpClient:
    """Stand-in for ClickUpClient returning canned tasks."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def list_tasks(self, *, list_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            'tasks': [
                {
                    'id': 't1',
                    'name': 'Follow up',
                    'status': {'status': 'open'},
                    'url': 'https://app.clickup.com/t/t1',
                    'date_updated': '1750000000000',
                    'text_content': 'body',
                },
            ],
            'last_page': True,
        }


class TestClickUpSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('clickup')
        if adapter is None:
            raise RuntimeError('clickup adapter not registered')
        self.adapter = adapter

    def test_validate_config_requires_list_id(self) -> None:
        self.adapter.validate_config({'list_id': '901'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({})

    def test_poll_enqueues_envelope_with_ref(self) -> None:
        seen: list[tuple[dict[str, Any], str]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.clickup.ClickUpClient', _FakeClickUpClient):
            result = self.adapter.poll(
                config={'list_id': '901', 'team_id': '9', 'max_results': 50},
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
            )

        self.assertEqual(result.items_seen, 1)
        self.assertEqual(result.items_enqueued, 1)
        payload, external_id = seen[0]
        self.assertEqual(external_id, 't1')
        self.assertEqual(payload['ref'], {'service': 'clickup', 'resource_type': 'task', 'resource_id': 't1'})
        self.assertEqual(payload['data']['name'], 'Follow up')
        self.assertEqual(payload['data']['status'], 'open')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/sources/tests/test_clickup_adapter.py -v`
Expected: FAIL — `get_adapter('clickup')` returns `None`.

- [ ] **Step 3: Write the adapter**

`backend/libs/sources/adapters/clickup.py`:

```python
"""ClickUp source adapter: poll a list for tasks into a queue.

Filtering (list id, statuses, updated-after) lives in `config` — no triage logic here.
Emits the shared `{data, ref}` envelope so an agent can re-fetch the live task.
"""

from __future__ import annotations

from typing import Any

from libs.clients.clickup import ClickUpClient
from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter

_DEFAULT_MAX_RESULTS = 50


def _status_name(task: dict[str, Any]) -> Any:
    """Return the status label whether ClickUp returns a string or a `{status: ...}` object."""
    status = task.get('status')
    if isinstance(status, dict):
        return status.get('status')
    return status


class ClickUpSourceAdapter(SourceAdapter):
    adapter_type = 'clickup'
    credential_type = 'clickup'

    def validate_config(self, config: dict[str, Any]) -> None:
        """Require a non-empty `list_id`; validate optional `statuses`/`max_results`."""
        list_id = config.get('list_id')
        if not isinstance(list_id, str) or not list_id:
            raise ValueError('list_id must be a non-empty string')
        statuses = config.get('statuses', [])
        if not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses):
            raise ValueError('statuses must be a list of strings')
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int) or max_results < 1:
            raise ValueError('max_results must be a positive integer')

    def poll(
        self,
        *,
        config: dict[str, Any],
        put_item: PutItemCallback,
        credential_supplier: SecretSupplier | None,
    ) -> PollResult:
        """List tasks in the configured list and enqueue one `{data, ref}` envelope per task."""
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        client = ClickUpClient(token_supplier=credential_supplier or (lambda: None), config=config)
        resp = client.list_tasks(
            list_id=config['list_id'],
            statuses=tuple(config.get('statuses', [])),
            include_closed=config.get('include_closed', False),
        )
        tasks = resp.get('tasks', [])[:max_results]
        enqueued = 0
        for task in tasks:
            task_id = task['id']
            envelope = {
                'data': {
                    'id': task_id,
                    'name': task.get('name'),
                    'status': _status_name(task),
                    'list_id': config['list_id'],
                    'url': task.get('url'),
                    'date_updated': task.get('date_updated'),
                    'text_content': task.get('text_content'),
                },
                'ref': {'service': 'clickup', 'resource_type': 'task', 'resource_id': task_id},
            }
            result = put_item(payload=envelope, external_id=task_id)
            if result.created:
                enqueued += 1
        return PollResult(items_seen=len(tasks), items_enqueued=enqueued)
```

Auto-discovered by the sources registry — no registration line needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/sources/tests/test_clickup_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/libs/sources/adapters/clickup.py backend/libs/sources/tests/test_clickup_adapter.py
git commit -m "feat(sources): add clickup source adapter with {data, ref} envelope"
git fetch origin main && git rebase origin/main && git push
```

---

## S3 — ClickUp tool

**Files:**
- Create: `backend/libs/tools/tools/clickup.py`
- Create: `backend/libs/tools/tests/test_clickup_tool.py`
- Modify: `backend/apps/agents/tools_wiring.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write the failing tool tests**

`backend/libs/tools/tests/test_clickup_tool.py`:

```python
"""Unit tests for ClickUpTool (client stubbed)."""

from __future__ import annotations

from typing import Any

from libs.clients.clickup.errors import ClickUpNotFoundError
from libs.tools.tools.clickup import ClickUpTool

from olib.py.django.test.cases import OTestCase


class _FakeClickUpClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('list_tasks', kwargs))
        return {'tasks': [{'id': 't1'}], 'last_page': True}

    def get_task(self, task_id: str) -> dict[str, Any]:
        self.calls.append(('get_task', {'task_id': task_id}))
        if task_id == 'missing':
            raise ClickUpNotFoundError('no such task')
        return {'id': task_id, 'name': 'T'}

    def create_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('create_task', kwargs))
        return {'id': 't9', 'name': kwargs['name']}


class TestClickUpTool(OTestCase):
    def _bound(self, fake: _FakeClickUpClient):
        return ClickUpTool().bind(
            token_supplier=lambda: 'pk_test',
            config={'team_id': '9'},
            client_factory=lambda **kw: fake,
        )

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        fns = {f.name: f for f in ClickUpTool().functions()}
        self.assertEqual(
            set(fns),
            {'list_spaces', 'list_lists', 'list_tasks', 'get_task', 'create_task', 'update_task', 'create_comment', 'delete_task'},
        )
        self.assertTrue(fns['list_tasks'].readonly)
        self.assertTrue(fns['get_task'].readonly)
        self.assertFalse(fns['create_task'].readonly)
        self.assertFalse(fns['delete_task'].readonly)

    def test_create_task_maps_to_client(self) -> None:
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)
        out = invoke('create_task', {'list_id': '901', 'name': 'New'})
        self.assertEqual(out['id'], 't9')
        self.assertEqual(fake.calls[0][0], 'create_task')

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeClickUpClient()
        invoke = self._bound(fake)
        out = invoke('get_task', {'task_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/tools/tests/test_clickup_tool.py -v`
Expected: FAIL — `libs.tools.tools.clickup` does not exist.

- [ ] **Step 3: Write the tool**

`backend/libs/tools/tools/clickup.py`:

```python
"""ClickUp tool: map LLM-visible functions to ClickUpClient methods.

Full surface exposed (including delete); per-instance allow/deny gates it (deny delete in
examples). `ClickUpError`s map to the same `{ok, error}` failure result as the Gmail tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.clients.clickup import ClickUpClient
from libs.clients.clickup.errors import ClickUpAuthError, ClickUpError, ClickUpNotFoundError
from libs.tools.base import Tool, ToolFunction

_TASK_ID_DESC = 'ClickUp task id (from `list_tasks`/queue item `ref.resource_id`).'
_LIST_ID_DESC = 'ClickUp list id to create/list tasks in.'


def _failure(exc: ClickUpError) -> dict[str, Any]:
    """Map a ClickUpError to a uniform tool failure result (same shape as Gmail)."""
    if isinstance(exc, ClickUpNotFoundError):
        kind = 'not_found'
    elif isinstance(exc, ClickUpAuthError):
        kind = 'auth'
    else:
        kind = 'api'
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


class ClickUpTool(Tool):
    name = 'clickup'
    credential_type = 'clickup'

    def bind(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        client_factory: Callable[..., ClickUpClient] | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke closed over a ClickUpClient (`client_factory` is a test seam)."""
        cfg = config or {}
        factory = client_factory or (lambda **kw: ClickUpClient(**kw))
        client = factory(token_supplier=token_supplier, config=cfg)
        team_id = cfg.get('team_id')

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            try:
                return self._dispatch(client, team_id, function, arguments)
            except ClickUpError as exc:
                return _failure(exc)

        return invoke

    def _dispatch(
        self, client: ClickUpClient, team_id: str | None, function: str, arguments: dict[str, Any]
    ) -> Any:
        """Route one function call to the matching client method."""
        if function == 'list_spaces':
            return client.list_spaces(arguments.get('team_id') or team_id or '')
        if function == 'list_lists':
            return client.list_lists(arguments['space_id'])
        if function == 'list_tasks':
            return client.list_tasks(
                list_id=arguments['list_id'],
                statuses=tuple(arguments.get('statuses', [])),
            )
        if function == 'get_task':
            return client.get_task(arguments['task_id'])
        if function == 'create_task':
            return client.create_task(
                list_id=arguments['list_id'],
                name=arguments['name'],
                description=arguments.get('description'),
                status=arguments.get('status'),
            )
        if function == 'update_task':
            task_id = arguments.pop('task_id')
            return client.update_task(task_id, **arguments)
        if function == 'create_comment':
            return client.create_comment(arguments['task_id'], text=arguments['text'])
        if function == 'delete_task':
            return {'ok': True, **client.delete_task(arguments['task_id'])}
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')

    def functions(self) -> list[ToolFunction]:
        """LLM-visible ClickUp functions (handlers require `bind`)."""
        task_only = {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'description': _TASK_ID_DESC}},
            'required': ['task_id'],
        }
        return [
            ToolFunction('list_spaces', 'List spaces in a workspace.', {
                'type': 'object',
                'properties': {'team_id': {'type': 'string', 'description': 'Workspace id (defaults to config.team_id).'}},
                'required': [],
            }, self._unbound, readonly=True),
            ToolFunction('list_lists', 'List lists in a space.', {
                'type': 'object',
                'properties': {'space_id': {'type': 'string'}},
                'required': ['space_id'],
            }, self._unbound, readonly=True),
            ToolFunction('list_tasks', 'List tasks in a list.', {
                'type': 'object',
                'properties': {
                    'list_id': {'type': 'string', 'description': _LIST_ID_DESC},
                    'statuses': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['list_id'],
            }, self._unbound, readonly=True),
            ToolFunction('get_task', 'Fetch one task.', task_only, self._unbound, readonly=True),
            ToolFunction('create_task', 'Create a task in a list (INBOX routing).', {
                'type': 'object',
                'properties': {
                    'list_id': {'type': 'string', 'description': _LIST_ID_DESC},
                    'name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'status': {'type': 'string'},
                },
                'required': ['list_id', 'name'],
            }, self._unbound, readonly=False),
            ToolFunction('update_task', 'Update task fields.', {
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string', 'description': _TASK_ID_DESC},
                    'name': {'type': 'string'},
                    'status': {'type': 'string'},
                    'description': {'type': 'string'},
                },
                'required': ['task_id'],
            }, self._unbound, readonly=False),
            ToolFunction('create_comment', 'Add a comment to a task.', {
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string', 'description': _TASK_ID_DESC},
                    'text': {'type': 'string'},
                },
                'required': ['task_id', 'text'],
            }, self._unbound, readonly=False),
            ToolFunction('delete_task', 'Delete a task (deny by default).', task_only, self._unbound, readonly=False),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('clickup tool requires bind(token_supplier=..., config=...)')
```

- [ ] **Step 4: Run tool tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/tools/tests/test_clickup_tool.py -v`
Expected: PASS

- [ ] **Step 5: Register the tool + wiring round-trip test**

Add to `backend/apps/agents/tools_wiring.py` `wire_tools()`:

```python
    from libs.tools.tools.clickup import ClickUpTool

    register_tool('clickup', ClickUpTool())
```

Add to `backend/apps/agents/tests/test_tool_wiring.py`:

```python
    def test_clickup_tool_wires_with_config_and_credential(self) -> None:
        from unittest.mock import MagicMock

        instances = [
            ToolInstance(
                id='clickup',
                type='clickup',
                credential_ref='clickup',
                allow=['list_tasks'],
                config={'team_id': '9'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_tasks.return_value = {'tasks': [{'id': 't1'}], 'last_page': True}
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'pk_test'), \
                patch('libs.tools.tools.clickup.ClickUpClient', return_value=fake_client):
            bound = build_bound_tools(instances, user_id=1)
            out = bound['clickup'].invoke('list_tasks', {'list_id': '901'})
        self.assertEqual(out['tasks'], [{'id': 't1'}])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_tool_wiring.py -v -k clickup`
Expected: PASS

- [ ] **Step 7: Full gate**

Run: `./olib/scripts/orunr py test-all`
Expected: exit 0

- [ ] **Step 8: Commit and sync**

```bash
git add backend/libs/tools/tools/clickup.py backend/libs/tools/tests/test_clickup_tool.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "feat(tools): add gated clickup tool and register it"
git fetch origin main && git rebase origin/main && git push
```

---

## S4 — Example spec + docs

**Files:**
- Create: `backend/libs/agent_specs/examples/clickup-inbox.yaml`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Add the example spec**

`backend/libs/agent_specs/examples/clickup-inbox.yaml`:

```yaml
schema_version: 2
description: Illustrative ClickUp INBOX router
llm:
  provider: anthropic
  model: claude-3-5-sonnet
system_prompt: |
  Route items to the ClickUp INBOX list using the clickup tool.
tools:
  - id: clickup
    type: clickup
    credential_ref: clickup
    config:
      team_id: "9000000"
    allow: [list_spaces, list_lists, list_tasks, get_task, create_task, update_task, create_comment]
    deny: [delete_task]
queues:
  - id: clickup-inbox
    sources:
      - id: clickup-list
        type: clickup
        credential_ref: clickup
        config:
          team_id: "9000000"
          list_id: "901000000"
          statuses: [open]
          max_results: 50
triggers:
  - name: worker
    kind: queue
    queue: clickup-inbox
    prompt: Process this task.
    max_sessions: 2
  - name: manual
    kind: manual
```

- [ ] **Step 2: Add an explicit example-validation test**

`test_examples.py` uses per-example tests (no auto-iteration), so add one. Append to `AgentSpecsTests` in `backend/libs/agent_specs/tests/test_examples.py`:

```python
    def test_clickup_inbox_example_validates(self) -> None:
        spec = load_example('clickup-inbox')
        validate_spec_tools(spec)
        self.assertEqual(spec.tools[0].type, 'clickup')
        self.assertEqual(spec.tools[0].config['team_id'], '9000000')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'clickup')
```

Run: `./olib/scripts/orunr py test backend/libs/agent_specs/tests/test_examples.py -v -k clickup_inbox`
Expected: PASS — `wire_tools()` registers `clickup` at startup; the source adapter is auto-discovered.

- [ ] **Step 3: Update ARCHITECTURE.md**

Extend the "External integrations" subsection (added in Gmail S5) to list ClickUp under the same three-component anatomy: personal token as `type=clickup` credential, `config.team_id` addressing, `httpx` REST client, source polls a list. Keep it brief; link this spec.

- [ ] **Step 4: Commit and sync**

```bash
git add backend/libs/agent_specs/examples/clickup-inbox.yaml docs/ARCHITECTURE.md
git commit -m "docs(clickup): add example inbox router spec and architecture notes"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 5: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the ClickUp changes on the feature branch against this plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

**Files:** (review only — no edits unless user requests fixes)

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

Read `superpowers/requesting-code-review`. Dispatch reviewer subagent with `{DESCRIPTION}` (ClickUp client/source/tool), `{PLAN_OR_REQUIREMENTS}` (this `-plan.md` + `-design.md`), `{BASE_SHA}`/`{HEAD_SHA}` from Step 2. Scope the review to ClickUp files if Gmail was already reviewed separately.

- [ ] **Step 4: Write review file and report findings**

Write `docs/specs/2026-07-06-clickup-integration/2026-07-06-clickup-integration-review.md` per `review-file-template.md`. Summarize in chat. Stop unless the user asks for fixes.

- [ ] **Step 5: Track feedback**

Update **Status** in `*-review.md` to **Fixed** / **Rejected** (rationale in Notes) as the user responds.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. Because Gmail (spec 6) shares this branch, finish both together (or land Gmail first, then ClickUp). Do not check epic/spec boxes unless the user approves after review.

---

## Self-review (plan author)

- **Spec coverage:** anatomy reuse (S1–S3), personal-token auth + header (S1), client methods (S1/S3), source filtering + envelope (S2), tool surface + allow/deny + failure mapping (S3), example + docs (S4) — all covered.
- **Type consistency:** `ClickUpClient(token_supplier=, config=, transport=)`; `bind(token_supplier=, config=, client_factory=)`; envelope keys `data`/`ref`; failure shape identical to Gmail's `{ok, error:{kind, message}}`.
- **Placeholders:** none — every code step has concrete content (the one deliberately obfuscated import in S1 Step 3 is annotated to be replaced with a plain import).
- **Dependency:** `ToolInstance.config` reused from spec 6 S1 (flagged; not re-added).

---

## Out of scope

Inbox triage taxonomy/routing (spec 9), OAuth app install, webhooks, custom fields/attachments/time-tracking, Obsidian, Gmail (spec 6).

## References

- [Design](./2026-07-06-clickup-integration-design.md) · [Epic](../../epics/2026-07-03-inbox-cleanup.md)
- [Gmail plan (spec 6)](../2026-07-06-gmail-integration/2026-07-06-gmail-integration-plan.md) — shared `ToolInstance.config` + envelope
- [Key management (spec 1)](../2026-07-03-key-management/2026-07-03-key-management-design.md) · [Sources and queues (spec 3)](../2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-design.md)
