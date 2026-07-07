# Gmail library and tool Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **6 of 9** · Item: **Gmail library and tool**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. **Complete Step 0 before any code change** — checkout the feature branch, then ensure `-revision.md` exists. Do **not** read `-revision.md` during implementation unless the user explicitly asks. Steps use checkbox (`- [ ]`) syntax. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review`).

**Goal:** Ship the first external integration — a generic `libs/clients/gmail` client (service account + domain-wide delegation), a `gmail` source adapter that polls a mailbox into a queue, and a gated `gmail` tool — plus the shared `ToolInstance.config` platform field that ClickUp (spec 7) reuses.

**Architecture:** Three Django-free lib components (client / source adapter / tool) wired through existing seams: `apps.queues.tasks.poll_source` calls the adapter; `apps.agents.tool_wiring.build_bound_tools` binds the tool with a `token_supplier` + new `config`. Secrets resolve lazily per operation via `apps.keys.make_secret_supplier` (spec 1). Sources enqueue a uniform `{data, ref}` payload envelope.

**Tech Stack:** Django 5.2, Pydantic v2, `google-api-python-client` + `google-auth`, existing `libs/tools` + `libs/sources` registries, Celery beat poll.

**Branch:** `feat/2026-07-06-service-integrations`

**Design spec:** [`2026-07-06-gmail-integration-design.md`](./2026-07-06-gmail-integration-design.md)
**Arch rules:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) · [`AGENTS.local.md`](../../AGENTS.local.md)

> **Cross-spec note:** **S1 (ToolInstance.config + wiring) is a shared platform change and MUST land before ClickUp spec 7 starts.** Both specs share branch `feat/2026-07-06-service-integrations`.

---

## Step 0 — Pre-implementation (mandatory)

**Gate:** Do not start S1 until every checkbox here is done.

- [ ] **Step 0a: Checkout feature branch**

```bash
git checkout feat/2026-07-06-service-integrations || git checkout -b feat/2026-07-06-service-integrations
git branch --show-current   # must print feat/2026-07-06-service-integrations
```

Never implement on `main`, `master`, or the default branch.

- [ ] **Step 0b: Ensure review template exists**

Create `docs/specs/2026-07-06-gmail-integration/2026-07-06-gmail-integration-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md`. Leave review sections empty; do not read it again during implementation.

- [ ] **Step 0c: Commit plan (if uncommitted)**

```bash
git add docs/specs/2026-07-06-gmail-integration/
git commit -m "docs(gmail): add gmail integration plan"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

Skip 0c if already committed on the feature branch.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped `./olib/scripts/orunr py test backend/<path>` while iterating — see `ai/commands/py-checks.md`)
- Add dependencies by editing `backend/pyproject.toml`, then `./olib/scripts/orun py sync` (mutating `orun`, run on host)
- Test base: `OTestCase` from `olib.py.django.test.cases`
- **Parproc naming:** never use `error`, `exception`, `warning`, `notice`, `deprecated` in test names — use `failure`, `raises`, `invalid`, `bad_request`, `caution`, `legacy`
- **Lib rules (`AGENTS.local.md`):** `libs/*` never import `apps.*`; credentials injected at the app boundary as a `token_supplier`; never store plaintext on client state beyond one operation
- **Function documentation:** every new/changed function/method gets a brief docstring per [`AGENTS.md`](../../AGENTS.md)
- **No compatibility re-exports:** update imports to the canonical module; delete replaced files
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final below)
- **Git (after each stage commit):** `git fetch origin main && git rebase origin/main && git push` — stop on rebase conflicts

---

## File structure

```
backend/
  libs/
    clients/
      __init__.py               # NEW (namespace)
      gmail/
        __init__.py             # NEW — exports GmailClient + errors
        client.py               # NEW — GmailClient (generic Gmail API wrapper)
        errors.py               # NEW — GmailError hierarchy
        tests/
          __init__.py           # NEW
          test_client.py        # NEW
    sources/
      adapters/
        gmail.py                # NEW — GmailSourceAdapter (auto-discovered)
      tests/
        test_gmail_adapter.py   # NEW
    tools/
      tools/
        gmail.py                # NEW — GmailTool
      tests/
        test_gmail_tool.py      # NEW
    agent_spec/
      spec.py                   # MODIFY — add ToolInstance.config
    agent_specs/
      examples/
        gmail-triage.yaml       # NEW — illustrative example
  apps/
    agents/
      tool_wiring.py            # MODIFY — thread config into bind()
      tools_wiring.py           # MODIFY — register GmailTool
      tests/
        test_tool_wiring.py     # MODIFY — config threading + gmail wiring
  pyproject.toml                # MODIFY — add google deps
```

---

## S1 — Shared platform change: `ToolInstance.config` + wiring

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Modify: `backend/apps/agents/tool_wiring.py`
- Test: `backend/apps/agents/tests/test_spec.py` (or the existing schema test module), `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write failing test for `ToolInstance.config`**

Add to `backend/apps/agents/tests/test_spec.py` (create a small test class if none fits):

```python
from libs.agent_spec import ToolInstance


class TestToolInstanceConfig:
    def test_config_defaults_to_empty_dict(self) -> None:
        inst = ToolInstance(id='gmail-a', type='gmail')
        assert inst.config == {}

    def test_config_round_trips(self) -> None:
        inst = ToolInstance(id='gmail-a', type='gmail', config={'subject': 'me@example.com'})
        assert inst.config == {'subject': 'me@example.com'}
        assert inst.model_dump()['config'] == {'subject': 'me@example.com'}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v -k ToolInstanceConfig`
Expected: FAIL — `ToolInstance` has no `config` field / unexpected keyword.

- [ ] **Step 3: Add the field**

In `backend/libs/agent_spec/spec.py`, add to `ToolInstance` (keep field order: after `credential_ref`, before `allow`):

```python
class ToolInstance(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    credential_ref: str | None = None
    config: dict[str, Any] = {}  # non-secret per-instance addressing (e.g. gmail subject); symmetric with SourceSpec.config
    allow: list[str] = ['*']
    deny: list[str] = []
```

`Any` is already imported in `spec.py`. **No `schema_version` bump** — backward-compatible optional field with a default (per `AGENTS.local.md` "when to bump" rules).

- [ ] **Step 4: Run the test to verify it passes**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -v -k ToolInstanceConfig`
Expected: PASS

- [ ] **Step 5: Write failing test for wiring passing `config` into `bind`**

In `backend/apps/agents/tests/test_tool_wiring.py`, update `_EchoCredTool.bind` to accept `config` and add a test. Replace the `bind` method:

```python
    def bind(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Echo whether a token resolved and surface the injected config."""
        cfg = config or {}

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'ping':
                raise ValueError(function)
            token = token_supplier()
            return {'token_set': token is not None, 'subject': cfg.get('subject')}

        return invoke
```

Add a test method to `TestBuildBoundTools`:

```python
    def test_credential_tool_receives_instance_config(self) -> None:
        instances = [
            ToolInstance(
                id='gmail-a',
                type='echo_cred',
                allow=['ping'],
                config={'subject': 'me@example.com'},
            ),
        ]
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'tok'):
            bound = build_bound_tools(instances, user_id=1)
        out = bound['gmail-a'].invoke('ping', {})
        self.assertEqual(out, {'token_set': True, 'subject': 'me@example.com'})
```

- [ ] **Step 6: Run test to verify it fails**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_tool_wiring.py -v -k receives_instance_config`
Expected: FAIL — `bind()` called without `config`; `subject` is `None`.

- [ ] **Step 7: Thread `config` through `bind_tool_invoke`**

In `backend/apps/agents/tool_wiring.py`, update the token-supplier branch of `bind_tool_invoke` to pass instance config. Change the signature and the branch:

```python
def bind_tool_invoke(
    tool: Tool,
    *,
    token_supplier: Callable[[], str | None] | None,
    config: dict[str, Any] | None = None,
    user_id: int | None = None,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
) -> Callable[[str, dict[str, Any]], Any]:
    """Return a bound invoke for *tool*, injecting credentials/config or queue session context."""
    bind = getattr(tool, 'bind', None)
    if bind is not None:
        if tool.name == 'queue':
            return cast(
                Callable[[str, dict[str, Any]], Any],
                bind(user_id=user_id, agent_id=agent_id, session_id=session_id),
            )
        if token_supplier is not None:
            return cast(
                Callable[[str, dict[str, Any]], Any],
                bind(token_supplier=token_supplier, config=config),
            )
    return tool.invoke
```

In `build_bound_tools`, pass `config=inst.config` when calling `bind_tool_invoke`:

```python
        invoke = bind_tool_invoke(
            tool,
            token_supplier=supplier,
            config=inst.config,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_tool_wiring.py -v`
Expected: PASS (including the existing `test_credential_tool_uses_supplier` — `_EchoCredTool.bind` now accepts an optional `config`).

- [ ] **Step 9: Commit and sync (PR-ready chunk)**

```bash
git add backend/libs/agent_spec/spec.py backend/apps/agents/tool_wiring.py backend/apps/agents/tests/
git commit -m "feat(agents): add ToolInstance.config and thread it through tool wiring"
git fetch origin main && git rebase origin/main && git push
```

---

## S2 — Gmail client (`libs/clients/gmail`)

**Files:**
- Create: `backend/libs/clients/__init__.py`, `backend/libs/clients/gmail/__init__.py`, `backend/libs/clients/gmail/client.py`, `backend/libs/clients/gmail/errors.py`
- Create: `backend/libs/clients/gmail/tests/__init__.py`, `backend/libs/clients/gmail/tests/test_client.py`
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add dependencies**

Add to `backend/pyproject.toml` `dependencies` (pin to the latest resolvable at implementation time; example pins shown):

```toml
  "google-api-python-client==2.149.0",
  "google-auth==2.36.0",
```

Then:

```bash
./olib/scripts/orun py sync
```

- [ ] **Step 2: Write `errors.py`**

`backend/libs/clients/gmail/errors.py`:

```python
"""Typed Gmail client failures (mapped to tool/source failure results by callers)."""

from __future__ import annotations


class GmailError(Exception):
    """Base class for all Gmail client failures."""


class GmailAuthError(GmailError):
    """Service-account parse, impersonation, or scope authorization failure."""


class GmailNotFoundError(GmailError):
    """Referenced message or label does not exist."""


class GmailAPIError(GmailError):
    """Non-2xx Gmail API response other than auth/not-found."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
```

- [ ] **Step 3: Write the failing client tests**

`backend/libs/clients/gmail/tests/test_client.py` (fake the Gmail service via `MagicMock`; inject a `service_factory` so google is never called in tests):

```python
"""Unit tests for GmailClient (Gmail service faked via MagicMock)."""

from __future__ import annotations

from unittest.mock import MagicMock

from libs.clients.gmail.client import GmailClient
from libs.clients.gmail.errors import GmailAuthError

from olib.py.django.test.cases import OTestCase


def _client_with_service(service: MagicMock, *, token: str | None = '{"sa": true}') -> GmailClient:
    """Build a GmailClient whose service factory returns the supplied fake service."""
    return GmailClient(
        token_supplier=lambda: token,
        config={'subject': 'me@example.com'},
        service_factory=lambda raw, subject: service,
    )


class TestGmailClient(OTestCase):
    def test_list_messages_parses_ids_and_page_token(self) -> None:
        service = MagicMock()
        (service.users.return_value.messages.return_value.list.return_value.execute.return_value) = {
            'messages': [{'id': 'm1'}, {'id': 'm2'}],
            'nextPageToken': 'tok',
        }
        client = _client_with_service(service)
        out = client.list_messages(query='in:inbox', max_results=25)
        self.assertEqual(out['message_ids'], ['m1', 'm2'])
        self.assertEqual(out['next_page_token'], 'tok')
        service.users.return_value.messages.return_value.list.assert_called_once_with(
            userId='me', q='in:inbox', maxResults=25, pageToken=None
        )

    def test_list_messages_handles_empty(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        client = _client_with_service(service)
        out = client.list_messages(query='in:inbox')
        self.assertEqual(out['message_ids'], [])
        self.assertIsNone(out['next_page_token'])

    def test_archive_removes_inbox_label(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {'id': 'm1'}
        client = _client_with_service(service)
        client.archive('m1')
        service.users.return_value.messages.return_value.modify.assert_called_once_with(
            userId='me', id='m1', body={'addLabelIds': [], 'removeLabelIds': ['INBOX']}
        )

    def test_report_spam_adds_spam_removes_inbox(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {'id': 'm1'}
        client = _client_with_service(service)
        client.report_spam('m1')
        service.users.return_value.messages.return_value.modify.assert_called_once_with(
            userId='me', id='m1', body={'addLabelIds': ['SPAM'], 'removeLabelIds': ['INBOX']}
        )

    def test_missing_subject_raises_auth_failure(self) -> None:
        client = GmailClient(
            token_supplier=lambda: '{"sa": true}',
            config={},
            service_factory=lambda raw, subject: MagicMock(),
        )
        with self.assertRaises(GmailAuthError):
            client.list_messages(query='in:inbox')

    def test_missing_credential_raises_auth_failure(self) -> None:
        client = _client_with_service(MagicMock(), token=None)
        with self.assertRaises(GmailAuthError):
            client.list_messages(query='in:inbox')
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/clients/gmail/tests/test_client.py -v`
Expected: FAIL — module `libs.clients.gmail.client` does not exist.

- [ ] **Step 5: Write `client.py`**

`backend/libs/clients/gmail/client.py`:

```python
"""Generic Gmail API v1 client for one impersonated mailbox (Django-free).

Auth is service account + domain-wide delegation: the SA JSON is supplied lazily by
``token_supplier`` and impersonates ``config['subject']``. The client never stores the
plaintext credential or the built service beyond a single method call (secret-retention
rule, see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from libs.clients.gmail.errors import GmailAPIError, GmailAuthError, GmailNotFoundError

SCOPES = ('https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/gmail.send')

ServiceFactory = Callable[[str, str], Any]


def _build_service(raw_credential: str, subject: str) -> Any:
    """Build a Gmail API service impersonating *subject* from SA JSON (imports google lazily)."""
    from google.oauth2 import service_account  # noqa: PLC0415 — heavy optional dep, import on use
    from googleapiclient.discovery import build  # noqa: PLC0415

    try:
        info = json.loads(raw_credential)
    except (ValueError, TypeError) as exc:
        raise GmailAuthError('gmail credential is not valid service-account JSON') from exc
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=list(SCOPES))
        creds = creds.with_subject(subject)
    except Exception as exc:  # noqa: BLE001 — google raises assorted types on bad keys
        raise GmailAuthError(f'failed to build delegated credentials: {exc}') from exc
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


class GmailClient:
    """Thin wrapper over the Gmail API for a single impersonated mailbox."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        service_factory: ServiceFactory | None = None,
    ) -> None:
        self._token_supplier = token_supplier
        self._config = config or {}
        self._service_factory = service_factory or _build_service

    def _service(self) -> Any:
        """Resolve the credential and build a per-call impersonated service."""
        subject = self._config.get('subject')
        if not subject:
            raise GmailAuthError('config.subject (mailbox to impersonate) is required')
        raw = self._token_supplier()
        if not raw:
            raise GmailAuthError('no gmail credential resolved')
        return self._service_factory(raw, subject)

    def list_messages(
        self, *, query: str, max_results: int = 100, page_token: str | None = None
    ) -> dict[str, Any]:
        """Return `{message_ids, next_page_token}` for a Gmail search query."""
        resp = (
            self._service()
            .users()
            .messages()
            .list(userId='me', q=query, maxResults=max_results, pageToken=page_token)
            .execute()
        )
        return {
            'message_ids': [m['id'] for m in resp.get('messages', [])],
            'next_page_token': resp.get('nextPageToken'),
        }

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Fetch one message (`fmt` is 'metadata' or 'full')."""
        return (
            self._service()
            .users()
            .messages()
            .get(userId='me', id=message_id, format=fmt)
            .execute()
        )

    def list_labels(self) -> list[dict[str, Any]]:
        """Return label id/name records for the mailbox."""
        resp = self._service().users().labels().list(userId='me').execute()
        return list(resp.get('labels', []))

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Add/remove label ids on a message."""
        body = {'addLabelIds': list(add), 'removeLabelIds': list(remove)}
        return (
            self._service()
            .users()
            .messages()
            .modify(userId='me', id=message_id, body=body)
            .execute()
        )

    def archive(self, message_id: str) -> dict[str, Any]:
        """Remove the INBOX label (archive)."""
        return self.modify_labels(message_id, remove=('INBOX',))

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Move a message to spam."""
        return self.modify_labels(message_id, add=('SPAM',), remove=('INBOX',))

    def trash(self, message_id: str) -> dict[str, Any]:
        """Move a message to trash (denied by default in example configs)."""
        return self._service().users().messages().trash(userId='me', id=message_id).execute()
```

> **Note on error mapping:** the real `googleapiclient` raises `googleapiclient.errors.HttpError`.
> `get_message`/`modify_labels` etc. do not translate it here; the **tool and adapter** map any
> `GmailError` (and translate `HttpError`) to failure results in their layers (S3/S4). Keep the
> client focused on API calls; add `HttpError` → `GmailNotFoundError`/`GmailAPIError` translation
> in a small `_execute` helper only if S4 tests require it (see S4 Step 3). `GmailNotFoundError`
> is imported for that use.

- [ ] **Step 6: Write `__init__.py` files**

`backend/libs/clients/__init__.py`:

```python
"""Generic, Django-free API clients for external integrations (gmail, clickup, …)."""
```

`backend/libs/clients/gmail/__init__.py`:

```python
"""Gmail API client package."""

from libs.clients.gmail.client import GmailClient
from libs.clients.gmail.errors import (
    GmailAPIError,
    GmailAuthError,
    GmailError,
    GmailNotFoundError,
)

__all__ = [
    'GmailAPIError',
    'GmailAuthError',
    'GmailClient',
    'GmailError',
    'GmailNotFoundError',
]
```

`backend/libs/clients/gmail/tests/__init__.py`: empty file.

- [ ] **Step 7: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/clients/gmail/tests/test_client.py -v`
Expected: PASS

- [ ] **Step 8: Commit and sync**

```bash
git add backend/libs/clients/ backend/pyproject.toml backend/uv.lock
git commit -m "feat(clients): add generic Gmail API client with service-account delegation"
git fetch origin main && git rebase origin/main && git push
```

---

## S3 — Gmail source adapter

**Files:**
- Create: `backend/libs/sources/adapters/gmail.py`
- Create: `backend/libs/sources/tests/test_gmail_adapter.py`

- [ ] **Step 1: Write the failing adapter tests**

`backend/libs/sources/tests/test_gmail_adapter.py`:

```python
"""Tests for the Gmail source adapter (client stubbed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter

from olib.py.django.test.cases import OTestCase


class _FakeGmailClient:
    """Stand-in for GmailClient returning canned messages."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None):
        return {'message_ids': ['m1', 'm2'], 'next_page_token': None}

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        return {
            'id': message_id,
            'threadId': f't-{message_id}',
            'snippet': 'hello',
            'labelIds': ['INBOX'],
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'alice@example.com'},
                    {'name': 'Subject', 'value': 'Q3'},
                    {'name': 'Date', 'value': 'Mon, 06 Jul 2026 10:00:00 +0000'},
                ]
            },
        }


class TestGmailSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('gmail')
        if adapter is None:
            raise RuntimeError('gmail adapter not registered')
        self.adapter = adapter

    def test_validate_config_requires_subject_and_query(self) -> None:
        self.adapter.validate_config({'subject': 'me@example.com', 'query': 'in:inbox'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'query': 'in:inbox'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'subject': 'me@example.com'})

    def test_poll_enqueues_envelope_with_ref(self) -> None:
        seen: list[tuple[dict[str, Any], str]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.gmail.GmailClient', _FakeGmailClient):
            result = self.adapter.poll(
                config={'subject': 'me@example.com', 'query': 'in:inbox', 'max_results': 10},
                put_item=put_item,
                credential_supplier=lambda: '{"sa": true}',
            )

        self.assertEqual(result.items_seen, 2)
        self.assertEqual(result.items_enqueued, 2)
        payload, external_id = seen[0]
        self.assertEqual(external_id, 'm1')
        self.assertEqual(payload['ref'], {'service': 'gmail', 'resource_type': 'message', 'resource_id': 'm1'})
        self.assertEqual(payload['data']['from'], 'alice@example.com')
        self.assertEqual(payload['data']['subject'], 'Q3')
        self.assertEqual(payload['data']['thread_id'], 't-m1')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/sources/tests/test_gmail_adapter.py -v`
Expected: FAIL — `get_adapter('gmail')` returns `None` (adapter not created).

- [ ] **Step 3: Write the adapter**

`backend/libs/sources/adapters/gmail.py`:

```python
"""Gmail source adapter: poll a mailbox by search query into a queue.

Filtering (including U1's `-label:x-*` exclusion) lives entirely in `config.query` — the
adapter has no triage logic. Emits the shared `{data, ref}` payload envelope.
"""

from __future__ import annotations

from typing import Any

from libs.clients.gmail import GmailClient
from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter

_DEFAULT_MAX_RESULTS = 25


def _header(message: dict[str, Any], name: str) -> str | None:
    """Return a header value (case-insensitive) from a Gmail message payload."""
    for hdr in message.get('payload', {}).get('headers', []):
        if hdr.get('name', '').lower() == name.lower():
            return hdr.get('value')
    return None


class GmailSourceAdapter(SourceAdapter):
    adapter_type = 'gmail'
    credential_type = 'gmail'

    def validate_config(self, config: dict[str, Any]) -> None:
        """Require non-empty `subject` and `query`; validate `max_results` if present."""
        subject = config.get('subject')
        if not isinstance(subject, str) or not subject:
            raise ValueError('subject must be a non-empty string')
        query = config.get('query')
        if not isinstance(query, str) or not query:
            raise ValueError('query must be a non-empty string')
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
        """List messages by query and enqueue one `{data, ref}` envelope per message."""
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        client = GmailClient(token_supplier=credential_supplier or (lambda: None), config=config)
        listing = client.list_messages(query=config['query'], max_results=max_results)
        message_ids = listing['message_ids']
        enqueued = 0
        for message_id in message_ids:
            msg = client.get_message(message_id, fmt='metadata')
            envelope = {
                'data': {
                    'id': msg.get('id'),
                    'thread_id': msg.get('threadId'),
                    'from': _header(msg, 'From'),
                    'subject': _header(msg, 'Subject'),
                    'snippet': msg.get('snippet'),
                    'received_at': _header(msg, 'Date'),
                    'label_ids': msg.get('labelIds', []),
                },
                'ref': {'service': 'gmail', 'resource_type': 'message', 'resource_id': message_id},
            }
            result = put_item(payload=envelope, external_id=message_id)
            if result.created:
                enqueued += 1
        return PollResult(items_seen=len(message_ids), items_enqueued=enqueued)
```

The registry auto-discovers this file (`libs/sources/registry._discover_adapters`), so no registration line is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/sources/tests/test_gmail_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/libs/sources/adapters/gmail.py backend/libs/sources/tests/test_gmail_adapter.py
git commit -m "feat(sources): add gmail source adapter with {data, ref} envelope"
git fetch origin main && git rebase origin/main && git push
```

---

## S4 — Gmail tool

**Files:**
- Create: `backend/libs/tools/tools/gmail.py`
- Create: `backend/libs/tools/tests/test_gmail_tool.py`
- Modify: `backend/apps/agents/tools_wiring.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write the failing tool tests**

`backend/libs/tools/tests/test_gmail_tool.py`:

```python
"""Unit tests for GmailTool (client stubbed)."""

from __future__ import annotations

from typing import Any

from libs.clients.gmail.errors import GmailNotFoundError
from libs.tools.tools.gmail import GmailTool

from olib.py.django.test.cases import OTestCase


class _FakeGmailClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def list_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('list_messages', (), kwargs))
        return {'message_ids': ['m1'], 'next_page_token': None}

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        self.calls.append(('get_message', (message_id,), {'fmt': fmt}))
        if message_id == 'missing':
            raise GmailNotFoundError('no such message')
        return {'id': message_id, 'snippet': 'hi'}

    def modify_labels(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('modify_labels', (message_id,), kwargs))
        return {'id': message_id}

    def archive(self, message_id: str) -> dict[str, Any]:
        self.calls.append(('archive', (message_id,), {}))
        return {'id': message_id}


class TestGmailTool(OTestCase):
    def _bound(self, fake: _FakeGmailClient):
        tool = GmailTool()
        # Inject the fake by binding with a client_factory override for tests.
        return tool.bind(token_supplier=lambda: '{"sa": true}', config={'subject': 'me@example.com'}, client_factory=lambda **kw: fake)

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        fns = {f.name: f for f in GmailTool().functions()}
        self.assertEqual(
            set(fns),
            {'list', 'read', 'list_labels', 'get_attachment', 'label', 'archive', 'mark_spam', 'trash', 'send'},
        )
        self.assertTrue(fns['list'].readonly)
        self.assertTrue(fns['read'].readonly)
        self.assertFalse(fns['archive'].readonly)
        self.assertFalse(fns['send'].readonly)

    def test_list_maps_to_client(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out['message_ids'], ['m1'])
        self.assertEqual(fake.calls[0][0], 'list_messages')

    def test_archive_maps_to_client(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('archive', {'message_id': 'm1'})
        self.assertEqual(out, {'ok': True, 'id': 'm1'})
        self.assertEqual(fake.calls[0][0], 'archive')

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('read', {'message_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./olib/scripts/orunr py test backend/libs/tools/tests/test_gmail_tool.py -v`
Expected: FAIL — `libs.tools.tools.gmail` does not exist.

- [ ] **Step 3: Write the tool**

`backend/libs/tools/tools/gmail.py`:

```python
"""Gmail tool: map LLM-visible functions to GmailClient methods.

The full surface (including send/trash) is exposed; per-instance allow/deny gates it
(deny send/trash in example configs). Client `GmailError`s are mapped to a uniform
`{ok, error}` failure result shared with other integration tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.clients.gmail import GmailClient
from libs.clients.gmail.errors import GmailAuthError, GmailError, GmailNotFoundError
from libs.tools.base import Tool, ToolFunction

_MESSAGE_ID_DESC = 'Gmail message id (from `list`/queue item `ref.resource_id`).'


def _failure(exc: GmailError) -> dict[str, Any]:
    """Map a GmailError to a uniform tool failure result."""
    if isinstance(exc, GmailNotFoundError):
        kind = 'not_found'
    elif isinstance(exc, GmailAuthError):
        kind = 'auth'
    else:
        kind = 'api'
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


class GmailTool(Tool):
    name = 'gmail'
    credential_type = 'gmail'

    def bind(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        client_factory: Callable[..., GmailClient] | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke closed over a per-mailbox GmailClient.

        `client_factory` is a test seam; production uses the real GmailClient.
        """
        factory = client_factory or (lambda **kw: GmailClient(**kw))
        client = factory(token_supplier=token_supplier, config=config or {})

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            try:
                return self._dispatch(client, function, arguments)
            except GmailError as exc:
                return _failure(exc)

        return invoke

    def _dispatch(self, client: GmailClient, function: str, arguments: dict[str, Any]) -> Any:
        """Route one function call to the matching client method."""
        if function == 'list':
            return client.list_messages(
                query=arguments['query'],
                max_results=arguments.get('max_results', 100),
                page_token=arguments.get('page_token'),
            )
        if function == 'read':
            return client.get_message(arguments['message_id'], fmt='full')
        if function == 'list_labels':
            return {'labels': client.list_labels()}
        if function == 'get_attachment':
            return client.get_attachment(arguments['message_id'], arguments['attachment_id'])
        if function == 'label':
            return {
                'ok': True,
                **client.modify_labels(
                    arguments['message_id'],
                    add=tuple(arguments.get('add', [])),
                    remove=tuple(arguments.get('remove', [])),
                ),
            }
        if function == 'archive':
            return {'ok': True, **client.archive(arguments['message_id'])}
        if function == 'mark_spam':
            return {'ok': True, **client.report_spam(arguments['message_id'])}
        if function == 'trash':
            return {'ok': True, **client.trash(arguments['message_id'])}
        if function == 'send':
            return {'ok': True, **client.send_message(**arguments)}
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')

    def functions(self) -> list[ToolFunction]:
        """LLM-visible Gmail functions (handlers require `bind`)."""
        msg_only = {
            'type': 'object',
            'properties': {'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC}},
            'required': ['message_id'],
        }
        return [
            ToolFunction('list', 'Search messages by Gmail query.', {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Gmail search query, e.g. "in:inbox".'},
                    'max_results': {'type': 'integer'},
                    'page_token': {'type': 'string'},
                },
                'required': ['query'],
            }, self._unbound, readonly=True),
            ToolFunction('read', 'Read one message (full body).', msg_only, self._unbound, readonly=True),
            ToolFunction('list_labels', 'List label id/name pairs.', {
                'type': 'object', 'properties': {}, 'required': [],
            }, self._unbound, readonly=True),
            ToolFunction('get_attachment', 'Download an attachment (base64).', {
                'type': 'object',
                'properties': {
                    'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC},
                    'attachment_id': {'type': 'string'},
                },
                'required': ['message_id', 'attachment_id'],
            }, self._unbound, readonly=True),
            ToolFunction('label', 'Add/remove label ids on a message.', {
                'type': 'object',
                'properties': {
                    'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC},
                    'add': {'type': 'array', 'items': {'type': 'string'}},
                    'remove': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['message_id'],
            }, self._unbound, readonly=False),
            ToolFunction('archive', 'Archive (remove INBOX label).', msg_only, self._unbound, readonly=False),
            ToolFunction('mark_spam', 'Move message to spam.', msg_only, self._unbound, readonly=False),
            ToolFunction('trash', 'Move message to trash (deny by default).', msg_only, self._unbound, readonly=False),
            ToolFunction('send', 'Send a message (deny by default).', {
                'type': 'object',
                'properties': {
                    'to': {'type': 'string'},
                    'subject': {'type': 'string'},
                    'body': {'type': 'string'},
                },
                'required': ['to', 'subject', 'body'],
            }, self._unbound, readonly=False),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('gmail tool requires bind(token_supplier=..., config=...)')
```

> **Add missing client methods used above:** `get_attachment` and `send_message` are called by
> the tool but not written in S2. Add them to `GmailClient` now (small, tested via the tool's
> fake in this stage; a direct client test is optional):
>
> ```python
>     def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
>         """Return an attachment body record (base64 `data` + `size`)."""
>         return (
>             self._service().users().messages().attachments()
>             .get(userId='me', messageId=message_id, id=attachment_id).execute()
>         )
>
>     def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
>         """Send a plain-text message (requires the gmail.send scope; denied by default)."""
>         import base64
>         from email.message import EmailMessage
>
>         email = EmailMessage()
>         email['To'] = to
>         email['Subject'] = subject
>         email.set_content(body)
>         raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
>         return self._service().users().messages().send(userId='me', body={'raw': raw}).execute()
> ```

- [ ] **Step 4: Run tool tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/libs/tools/tests/test_gmail_tool.py -v`
Expected: PASS

- [ ] **Step 5: Register the tool + failing wiring test**

Add to `backend/apps/agents/tools_wiring.py` `wire_tools()`:

```python
    from libs.tools.tools.gmail import GmailTool

    register_tool('gmail', GmailTool())
```

Add a wiring round-trip test to `backend/apps/agents/tests/test_tool_wiring.py` (stubs the client via the tool's `client_factory`? No — `build_bound_tools` builds the real tool. Instead patch `GmailClient` where the tool imports it):

```python
    def test_gmail_tool_wires_with_config_and_credential(self) -> None:
        from unittest.mock import MagicMock

        instances = [
            ToolInstance(
                id='gmail-personal',
                type='gmail',
                credential_ref='gmail-personal',
                allow=['list'],
                config={'subject': 'me@example.com'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_messages.return_value = {'message_ids': ['m1'], 'next_page_token': None}
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: '{"sa": true}'), \
                patch('libs.tools.tools.gmail.GmailClient', return_value=fake_client):
            bound = build_bound_tools(instances, user_id=1)
            out = bound['gmail-personal'].invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out['message_ids'], ['m1'])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./olib/scripts/orunr py test backend/apps/agents/tests/test_tool_wiring.py -v -k gmail`
Expected: PASS (tool registered; `build_bound_tools` supplies `token_supplier` + `config`).

- [ ] **Step 7: Full gate**

Run: `./olib/scripts/orunr py test-all`
Expected: exit 0

- [ ] **Step 8: Commit and sync**

```bash
git add backend/libs/tools/tools/gmail.py backend/libs/tools/tests/test_gmail_tool.py backend/libs/clients/gmail/client.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "feat(tools): add gated gmail tool and register it"
git fetch origin main && git rebase origin/main && git push
```

---

## S5 — Example spec + docs

**Files:**
- Create: `backend/libs/agent_specs/examples/gmail-triage.yaml`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Add the example spec**

`backend/libs/agent_specs/examples/gmail-triage.yaml`:

```yaml
schema_version: 2
description: Illustrative Gmail triage agent (full agent is spec 9)
llm:
  provider: anthropic
  model: claude-3-5-sonnet
system_prompt: |
  Triage one email per session using the gmail tool.
tools:
  - id: gmail-personal
    type: gmail
    credential_ref: gmail-personal
    config:
      subject: me@example.com
    allow: [list, read, list_labels, get_attachment, label, archive, mark_spam]
    deny: [send, trash]
queues:
  - id: inbox
    sources:
      - id: gmail-main
        type: gmail
        credential_ref: gmail-personal
        config:
          subject: me@example.com
          query: "in:inbox -label:x-act -label:x-read -label:x-spam -label:x-unimp"
          max_results: 25
triggers:
  - name: inbox-worker
    kind: queue
    queue: inbox
    prompt: Triage this email.
    max_sessions: 2
  - name: manual
    kind: manual
```

- [ ] **Step 2: Add an explicit example-validation test**

`test_examples.py` uses per-example tests (no auto-iteration), so add one. Append to `AgentSpecsTests` in `backend/libs/agent_specs/tests/test_examples.py`:

```python
    def test_gmail_triage_example_validates(self) -> None:
        spec = load_example('gmail-triage')
        validate_spec_tools(spec)
        self.assertEqual(spec.tools[0].type, 'gmail')
        self.assertEqual(spec.tools[0].config['subject'], 'me@example.com')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'gmail')
```

Run: `./olib/scripts/orunr py test backend/libs/agent_specs/tests/test_examples.py -v -k gmail_triage`
Expected: PASS — `wire_tools()` runs at Django startup (registers `gmail`); the source adapter is auto-discovered. If `validate_spec_tools` does not exercise source adapters, that is fine — the adapter is unit-tested in S3.

- [ ] **Step 3: Update ARCHITECTURE.md**

Add an "External integrations" subsection to `docs/ARCHITECTURE.md` documenting: the three-component anatomy (client/source/tool), the `{data, ref}` queue payload envelope, `ToolInstance.config` for non-secret addressing, and the Gmail service-account + domain-wide-delegation setup (SA JSON stored as a `type=gmail` credential; `config.subject` selects the mailbox). Keep it brief; link this spec.

- [ ] **Step 4: Commit and sync**

```bash
git add backend/libs/agent_specs/examples/gmail-triage.yaml docs/ARCHITECTURE.md
git commit -m "docs(gmail): add example triage spec and architecture notes"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 6: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against this plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

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

Read `superpowers/requesting-code-review`. Dispatch reviewer subagent with `{DESCRIPTION}` (Gmail client/source/tool + `ToolInstance.config`), `{PLAN_OR_REQUIREMENTS}` (this `-plan.md` + `-design.md`), `{BASE_SHA}`/`{HEAD_SHA}` from Step 2.

- [ ] **Step 4: Write review file and report findings**

Write `docs/specs/2026-07-06-gmail-integration/2026-07-06-gmail-integration-review.md` per `review-file-template.md` (one table per severity: `#`, Status, Location, Finding, Notes). Summarize in chat. Stop unless the user asks for fixes.

- [ ] **Step 5: Track feedback**

Update **Status** in `*-review.md` to **Fixed** / **Rejected** (rationale in Notes) as the user responds.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. Do not check epic/spec boxes unless the user approves after review. **Note:** the ClickUp spec (7) shares this branch — coordinate the branch finish with spec 7.

---

## Self-review (plan author)

- **Spec coverage:** anatomy (S1–S4), auth/SA delegation (S2), client methods (S2/S4), source filtering + envelope (S3), tool surface + allow/deny + failure mapping (S4), example + docs (S5) — all covered.
- **Type consistency:** `GmailClient(token_supplier=, config=, service_factory=/client_factory=)`; envelope keys `data`/`ref` match the design; tool functions match the design's function table.
- **Placeholders:** none — every code step has concrete content.
- **Cross-spec:** S1 flagged as prerequisite for spec 7.

---

## Out of scope

Inbox triage taxonomy/routing (spec 9), OAuth consumer flow, Pub/Sub watch, MIME attachments on send, ClickUp/Obsidian.

## References

- [Design](./2026-07-06-gmail-integration-design.md) · [Epic](../../epics/2026-07-03-inbox-cleanup.md)
- [ClickUp plan (spec 7)](../2026-07-06-clickup-integration/2026-07-06-clickup-integration-plan.md)
- [Key management (spec 1)](../2026-07-03-key-management/2026-07-03-key-management-design.md) · [Sources and queues (spec 3)](../2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-design.md)
