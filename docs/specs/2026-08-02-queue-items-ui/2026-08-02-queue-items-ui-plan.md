# Queue Items UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Give operators a path from agent detail to a per-queue items table (all statuses, server-side filter/sort/pagination, live scoped refresh), built on a reusable filterable-table primitive shared by future list screens.

**Architecture:** A new Django-free `libs/web_tables` package owns the generic `TableSchema` / `TableQuery` / `ListPage` contract so both `apps.web` views and `apps.queues` domain queries can share it without a layering violation. `apps.queues.services.queries` gains a schema-driven `list_queue_items_page` and a `list_queue_summaries` counts query; `apps.queues.services.commands` publishes scoped `queues` resource hints (extending `apps.bus.resources`) after every mutating command. `apps.web` adds two new routes (queue items page + its htmx partial) plus an agent-scoped Queues partial, reusing the existing agent frame layout. Live refresh uses htmx's built-in `hx-trigger` event-filter + `delay` modifier against the existing SSE resource bridge — no new static JS asset.

**Tech Stack:** Django, Jinja2 (django-jinja) templates, htmx 2.0.3, Redis pub/sub SSE, `OTestCase` / `OTransactionTestCase`.

**Branch:** `feat/2026-08-02-queue-items-ui`

---

## Key implementation decisions (resolving design ambiguities)

These are concrete, already-decided answers to points the design left open for planning:

1. **Shared table types live in `libs/web_tables`, not `apps.web`.** `apps.queues.services.queries` must return a `ListPage`; if that type lived in `apps.web`, `apps.queues` would import `apps.web`, which is backwards (ARCHITECTURE.md: `apps.web` imports domain apps, never the reverse). A new Django-free `libs/web_tables` package (like `libs/agent_spec`) is importable by both without a layering violation.
2. **Page clamp is empty-safe.** `total == 0` → page `1` (empty result). `page > last` → clamp to last page. Implemented once via `libs.web_tables.clamp_page`.
3. **`q` filter bounded payload search:** `django.db.models.functions.Cast(payload, TextField())` wrapped in `Left(..., 2000)` (`PAYLOAD_SEARCH_TEXT_CAP`), OR'd with `external_id__icontains` / `failure_reason__icontains`.
4. **`taken_by` sorts by `taken_by_session_id`** (the session UUID), not a display name — no stable human label exists at the query layer.
5. **No new static JS asset.** Scoped, debounced live refetch uses htmx's built-in `hx-trigger` event filter (`chief:queues-changed[!event.detail.agentId || event.detail.agentId === '<id>'] from:body delay:600ms`) — this matches the zero-custom-JS pattern already used by `/partials/agents/` and `/partials/keys/`, and avoids adding a new file to the `backend/apps/web/static/web/` Vitest suite for a purely declarative behavior.
6. **Payload expand-state preservation** across an htmx swap uses one small inline `<script>` in `queue_items_table.html` (vanilla JS + a `data-expanded-id` attribute on the persistent wrapper `<div>`), matching the existing inline-script convention (`base.html`, `agent_config_history.html`) rather than a new tested JS module — this is a page-local DOM detail, not shared logic.
7. **`sync_from_spec` publishes one agent-scoped, queue-unscoped `queues` hint** per call (queue existence/counts may change across multiple queues at once) rather than one hint per queue.
8. **`release_stale_items` publishes one scoped hint per released/exhausted item** (not server-side coalesced) — acceptable because the client already debounces via `delay:600ms`.
9. **The queue items page reuses the full agent frame layout, including the chat box**, per the design's "same agent frame chrome" instruction — this avoids forking `web/layout/agent_frame_page.html`.
10. **Filter/sort/pagination navigation is a plain GET (full page reload).** Only the live-SSE-triggered refetch uses htmx. This keeps the URL always authoritative/bookmarkable and avoids duplicating query-string state in JS.
11. **Full payload JSON is rendered server-side for every row** (bounded to 64KB per item by the existing `MAX_PAYLOAD_BYTES` write-time cap), toggled via a CSS class — no lazy-fetch-on-expand endpoint. Acceptable for a 50-row page size.

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …` while iterating; `./olib/scripts/orunr py test-all` before every commit
- **Git:** plan/design docs only on `main`; implementation uses `feat/2026-08-02-queue-items-ui`. After each stage commit: `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** brief docstring (Python) on every new or materially changed function/method per `AGENTS.md` — purpose, assumptions, non-obvious logic
- **No compatibility re-exports:** update imports to canonical modules; delete replaced files — no shims (not expected to apply in this plan, but stated per convention)
- **Test bases:** `OTestCase` / `OTransactionTestCase` only for this plan — never bare `unittest.TestCase`
- **Test naming:** avoid `error`, `exception`, `warning`, `notice`, `deprecated`, `deprecation` in test names
- **Layers:** `apps.web` views never query the ORM directly — only `apps.web.services.queries` / `apps.queues.services.queries` (or other domain `services/queries.py` modules)
- **No new JS assets:** this plan adds zero files under `backend/apps/web/static/web/`; do not run `orunr js test-unit` / `orunr js lint` / `orunr js tsc` for this plan's changes (nothing under the JS root changes)
- **Final task:** **S_final** via `superpowers/requesting-code-review`

Focused Django test runs (adjust per task):

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_resources.py -v
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py -v
./olib/scripts/orunr py test backend/libs/web_tables/tests/ -v
./olib/scripts/orunr py test backend/apps/queues/tests/test_queries.py backend/apps/queues/tests/test_commands.py -v
./olib/scripts/orunr py test backend/apps/web/tests/test_queue_items.py backend/apps/web/tests/test_agent_detail.py -v
```

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `backend/libs/web_tables/__init__.py` | Package exports (`TableSchema`, `TableQuery`, `parse_table_query`, `ListPage`, `clamp_page`) |
| `backend/libs/web_tables/schema.py` | `SortDir`, `TableSchema` — allowlisted sort keys, defaults, page size, filter keys (Django-free) |
| `backend/libs/web_tables/query.py` | `TableQuery`, `parse_table_query` — validated GET-param parsing with safe fallbacks |
| `backend/libs/web_tables/list_page.py` | `ListPage` generic DTO, `clamp_page`, `ListPage.query_string()` link-building helper |
| `backend/libs/web_tables/tests/test_query.py` | Unit tests: schema validation, sort/dir/page fallbacks, filter echo |
| `backend/libs/web_tables/tests/test_list_page.py` | Unit tests: `clamp_page`, `total_pages`, indices, `query_string()` |
| `backend/apps/bus/resources.py` | *Modify* — add `'queues'` to `ResourceName`/`RESOURCE_NAMES`; add optional `agent_id`/`queue_id` kwargs to `resource_message`/`publish_resource_update`/`publish_resource_update_after_commit` |
| `backend/apps/bus/tests/test_resources.py` | *Modify* — cover the `queues` resource and scoped id round-trip |
| `backend/apps/web/resource_events.py` | *Modify* — pass through valid UUID-string `agent_id`/`queue_id` in `_validated_resource_message` |
| `backend/apps/web/tests/test_resource_events.py` | *Modify* — cover scoped-id pass-through/rejection and the updated `base.html` script |
| `backend/templates/web/base.html` | *Modify* — map `queues` → `chief:queues-changed`; forward `agent_id`/`queue_id` as event `detail` |
| `backend/apps/queues/services/queries.py` | *Modify* — add `QUEUE_ITEMS_TABLE_SCHEMA`, `QueueSummary`, `list_queue_summaries`, `list_source_ids`, `list_queue_items_page` |
| `backend/apps/queues/tests/test_queries.py` | *Modify* — cover the new queries |
| `backend/apps/queues/services/commands.py` | *Modify* — publish scoped `queues` hints from `put_item`/`take_item`/`complete_item`/`fail_item`/`release_stale_items`/`sync_from_spec` |
| `backend/apps/queues/tests/test_commands.py` | *Modify* — cover hint publishing per command |
| `backend/apps/web/views.py` | *Modify* — add `agent_queues_partial`, `queue_items`, `queue_items_partial`; extend `agent_detail` with queue summaries |
| `backend/apps/web/urls.py` | *Modify* — add the three new routes |
| `backend/apps/web/tests/test_queue_items.py` | New view/ownership/filter/sort/pagination/partial tests |
| `backend/templates/web/macros/table.html` | New reusable macros: `sort_link`, `pagination`, `filter_form_open`, `filter_form_close` |
| `backend/templates/web/partials/agent_queues.html` | New agent-detail Queues section fragment |
| `backend/templates/web/partials/queue_items_table.html` | New queue items table fragment (shell + rows + expand script) — the htmx swap target's inner content |
| `backend/templates/web/queue_items.html` | New full queue items page |
| `backend/templates/web/agent_detail.html` | *Modify* — embed the Queues section (htmx-swappable) |
| `backend/templates/web/partials/agent_frame_styles.html` | *Modify* — generic filter-form/sort-link/pagination CSS |
| `backend/templates/web/base.html` (styles) | *Modify* — add `.pill.available` / `.pill.taken` / `.pill.exhausted` |
| `docs/ARCHITECTURE.md` | *Modify* — `apps.queues` → `bus` import edge; `libs/web_tables`; `queues` resource + scoping docs; cross-link this spec |
| `docs/specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-review.md` | Created at S_final by the code-review subagent |

---

### Task 1: Bus — `queues` resource + scoped envelope fields

**Files:**
- Modify: `backend/apps/bus/resources.py`
- Test: `backend/apps/bus/tests/test_resources.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/apps/bus/tests/test_resources.py` (add `import uuid` near the top, alongside the existing `import json`):

```python
import json
import uuid
from unittest.mock import MagicMock, patch
```

Change the existing resource-name loop to include the new resource:

```python
    def test_resource_message_accepts_each_resource(self) -> None:
        """Return the exact generic envelope for every supported resource."""
        for resource in ('agents', 'keys', 'queues'):
            with self.subTest(resource=resource):
                self.assertEqual(resource_message(resource), {'channel': 'resource_update', 'resource': resource})
```

Add new test methods to `TestResourceEvents`:

```python
    def test_resource_message_includes_scoped_ids_when_provided(self) -> None:
        """Include agent_id/queue_id as strings only when the caller supplies them."""
        agent_id = uuid.uuid4()
        queue_id = uuid.uuid4()
        self.assertEqual(
            resource_message('queues', agent_id=agent_id, queue_id=queue_id),
            {
                'channel': 'resource_update',
                'resource': 'queues',
                'agent_id': str(agent_id),
                'queue_id': str(queue_id),
            },
        )

    def test_resource_message_omits_scoped_ids_when_not_provided(self) -> None:
        """Keep the envelope minimal for calls that pass no scoping."""
        self.assertEqual(resource_message('agents'), {'channel': 'resource_update', 'resource': 'agents'})

    @patch('apps.bus.resources.sync_client')
    def test_publish_resource_update_forwards_scoped_ids(self, mock_sync: MagicMock) -> None:
        """Serialize scoped ids into the published JSON envelope."""
        client = mock_sync.return_value
        agent_id = uuid.uuid4()

        publish_resource_update(42, 'queues', agent_id=agent_id)

        client.publish.assert_called_once_with(
            'test:user:42:resources',
            json.dumps({'channel': 'resource_update', 'resource': 'queues', 'agent_id': str(agent_id)}),
        )

    @patch('apps.bus.resources.publish_resource_update')
    def test_after_commit_forwards_scoped_ids(self, publish: MagicMock) -> None:
        """Defer scoped-id publishing to commit, same as the unscoped case."""
        agent_id = uuid.uuid4()
        queue_id = uuid.uuid4()
        with self.captureOnCommitCallbacks(execute=True):
            publish_resource_update_after_commit(42, 'queues', agent_id=agent_id, queue_id=queue_id)
        publish.assert_called_once_with(42, 'queues', agent_id=agent_id, queue_id=queue_id)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_resources.py -v
```

Expected: FAIL — `resource_message()` raises `ValueError: Unknown resource: queues` and/or `TypeError: unexpected keyword argument 'agent_id'`.

- [ ] **Step 3: Implement the scoped envelope in `apps/bus/resources.py`**

Replace the full file contents:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""User-scoped resource update events for Redis pub/sub."""

from __future__ import annotations

import json
import logging
from typing import Literal
from uuid import UUID

from apps.bus.client import key_prefix, sync_client
from django.db import transaction

ResourceName = Literal['agents', 'keys', 'queues']
RESOURCE_NAMES: frozenset[ResourceName] = frozenset(('agents', 'keys', 'queues'))
logger = logging.getLogger(__name__)


def user_resource_channel(user_id: int) -> str:
    """Return the cache-prefixed resource channel for a user."""
    return f'{key_prefix()}user:{user_id}:resources'


def resource_message(
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> dict[str, str]:
    """Validate a resource name and return its update envelope.

    ``agent_id`` / ``queue_id`` are optional scoping hints (currently used by the
    ``queues`` resource). Omitted values are left out of the envelope entirely
    (never sent as null), so unscoped calls produce the same minimal shape as
    before this scoping was added.
    """
    if resource not in RESOURCE_NAMES:
        raise ValueError(f'Unknown resource: {resource}')
    message: dict[str, str] = {'channel': 'resource_update', 'resource': resource}
    if agent_id is not None:
        message['agent_id'] = str(agent_id)
    if queue_id is not None:
        message['queue_id'] = str(queue_id)
    return message


def publish_resource_update(
    user_id: int,
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> None:
    """Publish a resource update envelope to the user's channel."""
    sync_client().publish(
        user_resource_channel(user_id),
        json.dumps(resource_message(resource, agent_id=agent_id, queue_id=queue_id)),
    )


def publish_resource_update_after_commit(
    user_id: int,
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> None:
    """Schedule a best-effort typed refresh hint after the write commits."""

    def publish() -> None:
        """Keep refresh transport failure independent from authoritative state."""
        try:
            publish_resource_update(user_id, resource, agent_id=agent_id, queue_id=queue_id)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug('Resource refresh transport unavailable')

    transaction.on_commit(publish, robust=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/apps/bus/tests/test_resources.py -v
```

Expected: PASS

- [ ] **Step 5: Commit and sync (PR-ready chunk)**

```bash
git add backend/apps/bus/resources.py backend/apps/bus/tests/test_resources.py
git commit -m "feat: add queues resource and scoped ids to the resource_update envelope"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 2: Web SSE — validate scoped ids + wire the `queues` client event

**Files:**
- Modify: `backend/apps/web/resource_events.py`
- Modify: `backend/templates/web/base.html`
- Test: `backend/apps/web/tests/test_resource_events.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/apps/web/tests/test_resource_events.py`, inside `TestResourceEventsSse` (after `test_stream_skips_malformed_and_unknown_messages`):

```python
    def test_stream_forwards_valid_scoped_ids_for_queues(self) -> None:
        """Pass through well-formed UUID-string agent_id/queue_id for the queues resource."""
        agent_id = '11111111-1111-4111-8111-111111111111'
        queue_id = '22222222-2222-4222-8222-222222222222'
        redis = FakeRedis(
            [
                {
                    'type': 'message',
                    'data': json.dumps(
                        {
                            'channel': 'resource_update',
                            'resource': 'queues',
                            'agent_id': agent_id,
                            'queue_id': queue_id,
                        }
                    ),
                }
            ]
        )

        async def collect() -> str:
            """Consume and close the one scoped queues event."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
            return chunk.decode()

        chunk = asyncio.run(collect())
        self.assertEqual(
            chunk,
            'event: resource_update\ndata: '
            + json.dumps(
                {
                    'channel': 'resource_update',
                    'resource': 'queues',
                    'agent_id': agent_id,
                    'queue_id': queue_id,
                }
            )
            + '\n\n',
        )

    def test_stream_drops_invalid_scoped_ids(self) -> None:
        """Emit the envelope without agent_id/queue_id when either is not a valid UUID string."""
        redis = FakeRedis(
            [
                {
                    'type': 'message',
                    'data': json.dumps(
                        {
                            'channel': 'resource_update',
                            'resource': 'queues',
                            'agent_id': 'not-a-uuid',
                            'queue_id': 12345,
                        }
                    ),
                }
            ]
        )

        async def collect() -> str:
            """Consume and close the one sanitized queues event."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
            return chunk.decode()

        chunk = asyncio.run(collect())
        self.assertEqual(
            chunk,
            'event: resource_update\ndata: {"channel": "resource_update", "resource": "queues"}\n\n',
        )
```

Add a new import at the top of that test module (json is already imported).

Add a new test in `TestResourceEventsTemplate`:

```python
    def test_authenticated_page_maps_queues_resource_with_scoped_detail(self) -> None:
        """Wire the queues resource to its event name and forward scoped ids as event detail."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        body = response.content.decode()

        self.assertIn("queues: 'chief:queues-changed'", body)
        self.assertIn('htmx.trigger(document.body, eventName, {', body)
        self.assertIn('agentId: message.agent_id', body)
        self.assertIn('queueId: message.queue_id', body)
```

Update the existing assertion that will now be a substring of a longer call (in `test_authenticated_page_contains_one_safe_event_source`), replacing:

```python
        self.assertIn('htmx.trigger(document.body, eventName)', body)
```

with:

```python
        self.assertIn('htmx.trigger(document.body, eventName, {', body)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py -v
```

Expected: FAIL — scoped ids are silently dropped today (message shape mismatch), and the template does not yet contain `chief:queues-changed` or the detail-forwarding call shape.

- [ ] **Step 3: Pass through valid scoped ids in `apps/web/resource_events.py`**

Modify the imports and `_validated_resource_message` function:

```python
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast
from uuid import UUID

from apps.bus.client import async_client
from apps.bus.resources import RESOURCE_NAMES, user_resource_channel
```

Add a helper and update the validator:

```python
def _valid_uuid_string(value: Any) -> str | None:
    """Return *value* unchanged when it is a syntactically valid UUID string, else None."""
    if not isinstance(value, str):
        return None
    try:
        UUID(value)
    except ValueError:
        return None
    return value


def _validated_resource_message(data: Any) -> dict[str, str] | None:
    """Validate and canonicalize the public envelope without retaining extra data."""
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        logger.debug('Skipping malformed resource refresh message')
        return None
    if not isinstance(raw, dict) or raw.get('channel') != 'resource_update':
        logger.debug('Skipping unknown resource refresh message')
        return None
    resource = raw.get('resource')
    if not isinstance(resource, str) or resource not in RESOURCE_NAMES:
        logger.debug('Skipping unknown resource refresh message')
        return None
    message: dict[str, str] = {'channel': 'resource_update', 'resource': resource}
    agent_id = _valid_uuid_string(raw.get('agent_id'))
    if agent_id is not None:
        message['agent_id'] = agent_id
    queue_id = _valid_uuid_string(raw.get('queue_id'))
    if queue_id is not None:
        message['queue_id'] = queue_id
    return message
```

- [ ] **Step 4: Wire the `queues` client event in `templates/web/base.html`**

Replace the `resource_update` listener body:

```html
      resourceEvents.addEventListener('resource_update', (event) => {
        try {
          const message = JSON.parse(event.data);
          const eventName = {
            agents: 'chief:agents-changed',
            keys: 'chief:keys-changed',
            queues: 'chief:queues-changed',
          }[message.resource];
          if (message.channel === 'resource_update' && eventName) {
            htmx.trigger(document.body, eventName, {
              agentId: message.agent_id || null,
              queueId: message.queue_id || null,
            });
          }
        } catch {
          // Invalid hints are non-authoritative and contain nothing safe to log.
        }
      });
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_resource_events.py -v
```

Expected: PASS

- [ ] **Step 6: Run the full web suite to catch other assertion drift**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/ -v
```

Expected: PASS (confirms no other test asserted the exact old `htmx.trigger(document.body, eventName)` call shape)

- [ ] **Step 7: Commit and sync (PR-ready chunk)**

```bash
git add backend/apps/web/resource_events.py backend/apps/web/tests/test_resource_events.py backend/templates/web/base.html
git commit -m "feat: forward scoped queues resource hints through the SSE bridge"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 3: `libs/web_tables` — generic table query contract (Django-free)

**Files:**
- Create: `backend/libs/web_tables/__init__.py`
- Create: `backend/libs/web_tables/schema.py`
- Create: `backend/libs/web_tables/query.py`
- Create: `backend/libs/web_tables/list_page.py`
- Create: `backend/libs/web_tables/tests/__init__.py`
- Create: `backend/libs/web_tables/tests/test_query.py`
- Create: `backend/libs/web_tables/tests/test_list_page.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/libs/web_tables/tests/__init__.py`:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
```

Create `backend/libs/web_tables/tests/test_query.py`:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for TableSchema validation and TableQuery parsing."""

from __future__ import annotations

from libs.web_tables.query import TableQuery, parse_table_query
from libs.web_tables.schema import TableSchema

from olib.py.django.test.cases import OTestCase


def _schema(**overrides: object) -> TableSchema:
    """Build a small TableSchema for tests, with sane overridable defaults."""
    defaults: dict[str, object] = {
        'sort_keys': frozenset({'created_at', 'status'}),
        'default_sort': 'created_at',
        'default_dir': 'desc',
        'page_size': 50,
        'filter_keys': frozenset({'status', 'q'}),
    }
    defaults.update(overrides)
    return TableSchema(**defaults)  # type: ignore[arg-type]


class TestTableSchema(OTestCase):
    def test_rejects_default_sort_outside_allowlist(self) -> None:
        with self.assertRaises(ValueError):
            TableSchema(sort_keys=frozenset({'a'}), default_sort='b', default_dir='asc', page_size=10)

    def test_rejects_non_positive_page_size(self) -> None:
        with self.assertRaises(ValueError):
            TableSchema(sort_keys=frozenset({'a'}), default_sort='a', default_dir='asc', page_size=0)


class TestParseTableQuery(OTestCase):
    def test_defaults_when_params_are_empty(self) -> None:
        query = parse_table_query({}, _schema())
        self.assertEqual(query, TableQuery(sort='created_at', dir='desc', page=1, filters={}))

    def test_accepts_allowlisted_sort_and_dir(self) -> None:
        query = parse_table_query({'sort': 'status', 'dir': 'asc'}, _schema())
        self.assertEqual(query.sort, 'status')
        self.assertEqual(query.dir, 'asc')

    def test_falls_back_to_default_sort_for_unknown_key(self) -> None:
        query = parse_table_query({'sort': 'unknown-column'}, _schema())
        self.assertEqual(query.sort, 'created_at')

    def test_falls_back_to_default_dir_for_invalid_value(self) -> None:
        query = parse_table_query({'dir': 'sideways'}, _schema())
        self.assertEqual(query.dir, 'desc')

    def test_falls_back_to_page_one_for_non_numeric_page(self) -> None:
        query = parse_table_query({'page': 'nope'}, _schema())
        self.assertEqual(query.page, 1)

    def test_falls_back_to_page_one_for_non_positive_page(self) -> None:
        self.assertEqual(parse_table_query({'page': '0'}, _schema()).page, 1)
        self.assertEqual(parse_table_query({'page': '-3'}, _schema()).page, 1)

    def test_accepts_valid_page_number(self) -> None:
        query = parse_table_query({'page': '4'}, _schema())
        self.assertEqual(query.page, 4)

    def test_echoes_only_declared_non_empty_filters(self) -> None:
        query = parse_table_query({'status': 'taken', 'source': 'ignored-undeclared', 'q': '  '}, _schema())
        self.assertEqual(query.filters, {'status': 'taken'})
```

Create `backend/libs/web_tables/tests/test_list_page.py`:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the ListPage DTO and clamp_page."""

from __future__ import annotations

from libs.web_tables.list_page import ListPage, clamp_page

from olib.py.django.test.cases import OTestCase


def _page(**overrides: object) -> ListPage[object]:
    """Build a small ListPage for tests, with sane overridable defaults."""
    defaults: dict[str, object] = {
        'rows': [],
        'total': 0,
        'page': 1,
        'page_size': 50,
        'sort': 'created_at',
        'dir': 'desc',
        'filters': {},
    }
    defaults.update(overrides)
    return ListPage(**defaults)  # type: ignore[arg-type]


class TestClampPage(OTestCase):
    def test_clamps_high_page_to_last_page(self) -> None:
        self.assertEqual(clamp_page(99, 3), 3)

    def test_clamps_low_page_to_one(self) -> None:
        self.assertEqual(clamp_page(0, 3), 1)
        self.assertEqual(clamp_page(-5, 3), 1)

    def test_clamps_to_one_when_no_pages_exist(self) -> None:
        self.assertEqual(clamp_page(5, 0), 1)

    def test_returns_page_unchanged_when_in_range(self) -> None:
        self.assertEqual(clamp_page(2, 3), 2)


class TestListPage(OTestCase):
    def test_total_pages_is_one_for_empty_table(self) -> None:
        self.assertEqual(_page(total=0, page_size=50).total_pages, 1)

    def test_total_pages_rounds_up(self) -> None:
        self.assertEqual(_page(total=101, page_size=50).total_pages, 3)

    def test_has_previous_and_next_on_a_middle_page(self) -> None:
        page = _page(total=150, page_size=50, page=2)
        self.assertTrue(page.has_previous)
        self.assertTrue(page.has_next)

    def test_first_and_last_page_boundaries(self) -> None:
        first = _page(total=150, page_size=50, page=1)
        last = _page(total=150, page_size=50, page=3)
        self.assertFalse(first.has_previous)
        self.assertFalse(last.has_next)

    def test_start_and_end_index_for_full_page(self) -> None:
        page = _page(total=150, page_size=50, page=2)
        self.assertEqual(page.start_index, 51)
        self.assertEqual(page.end_index, 100)

    def test_start_and_end_index_for_partial_last_page(self) -> None:
        page = _page(total=101, page_size=50, page=3)
        self.assertEqual(page.start_index, 101)
        self.assertEqual(page.end_index, 101)

    def test_start_and_end_index_are_zero_when_empty(self) -> None:
        page = _page(total=0)
        self.assertEqual(page.start_index, 0)
        self.assertEqual(page.end_index, 0)

    def test_query_string_encodes_state_and_filters(self) -> None:
        page = _page(sort='created_at', dir='desc', page=2, filters={'status': 'taken'})
        self.assertEqual(page.query_string(), 'sort=created_at&dir=desc&page=2&status=taken')

    def test_query_string_applies_overrides_without_mutating_page(self) -> None:
        page = _page(sort='created_at', dir='desc', page=2, filters={'status': 'taken'})
        self.assertEqual(
            page.query_string(sort='status', dir='asc', page=1),
            'sort=status&dir=asc&page=1&status=taken',
        )
        self.assertEqual(page.sort, 'created_at')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/libs/web_tables/tests/ -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'libs.web_tables'`

- [ ] **Step 3: Implement `libs/web_tables/schema.py`**

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Table schema declarations for the generic filterable-table query contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SortDir = Literal['asc', 'desc']


@dataclass(frozen=True, slots=True)
class TableSchema:
    """Declare one table's allowlisted sort keys, defaults, page size, and filter keys.

    Sort keys are opaque strings the caller controls (e.g. UI column ids); mapping
    a sort key to a concrete ORM ``order_by`` expression is the domain query's job,
    not this Django-free schema's job.
    """

    sort_keys: frozenset[str]
    default_sort: str
    default_dir: SortDir
    page_size: int
    filter_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Fail fast on an internally inconsistent schema rather than at query time."""
        if self.default_sort not in self.sort_keys:
            raise ValueError(f'default_sort {self.default_sort!r} must be one of sort_keys')
        if self.page_size < 1:
            raise ValueError('page_size must be at least 1')
```

- [ ] **Step 4: Implement `libs/web_tables/query.py`**

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse raw GET-style query parameters into a validated TableQuery for one TableSchema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from libs.web_tables.schema import SortDir, TableSchema


@dataclass(frozen=True, slots=True)
class TableQuery:
    """Validated, schema-allowlisted sort/dir/page/filter state parsed from a request."""

    sort: str
    dir: SortDir
    page: int
    filters: dict[str, str] = field(default_factory=dict)


def parse_table_query(params: Mapping[str, str], schema: TableSchema) -> TableQuery:
    """Parse GET-style *params* into a TableQuery, falling back to *schema* defaults.

    Unknown sort keys, invalid dir values, and non-positive/non-numeric page values
    fall back to schema defaults instead of raising, so a malformed query string
    never turns a list page into a 500. Only ``schema.filter_keys`` are echoed, and
    only when the value is non-empty after stripping whitespace.
    """
    sort = params.get('sort', schema.default_sort)
    if sort not in schema.sort_keys:
        sort = schema.default_sort

    raw_dir = params.get('dir', schema.default_dir)
    dir_: SortDir = raw_dir if raw_dir in ('asc', 'desc') else schema.default_dir  # type: ignore[assignment]

    try:
        page = int(params.get('page', '1'))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    filters: dict[str, str] = {}
    for key in schema.filter_keys:
        value = params.get(key, '').strip()
        if value:
            filters[key] = value

    return TableQuery(sort=sort, dir=dir_, page=page, filters=filters)
```

- [ ] **Step 5: Implement `libs/web_tables/list_page.py`**

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Paginated table result DTO shared by domain queries and web views/templates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar
from urllib.parse import urlencode

from libs.web_tables.schema import SortDir

RowT = TypeVar('RowT')


def clamp_page(page: int, total_pages: int) -> int:
    """Clamp a 1-based *page* into ``[1, total_pages]``; ``total_pages < 1`` clamps to 1."""
    if total_pages < 1:
        return 1
    return min(max(page, 1), total_pages)


@dataclass(frozen=True, slots=True)
class ListPage(Generic[RowT]):
    """One rendered page of a filtered/sorted table, plus the query state that produced it."""

    rows: Sequence[RowT]
    total: int
    page: int
    page_size: int
    sort: str
    dir: SortDir
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        """Total page count; always at least 1, even for an empty table."""
        if self.total <= 0:
            return 1
        return -(-self.total // self.page_size)

    @property
    def has_previous(self) -> bool:
        """Whether a page before the current one exists."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Whether a page after the current one exists."""
        return self.page < self.total_pages

    @property
    def start_index(self) -> int:
        """1-based index of this page's first row, or 0 when the table is empty."""
        if self.total == 0:
            return 0
        return (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        """1-based index of this page's last row, or 0 when the table is empty."""
        if self.total == 0:
            return 0
        return min(self.page * self.page_size, self.total)

    def query_string(
        self,
        *,
        sort: str | None = None,
        dir: SortDir | None = None,  # noqa: A002 - mirrors the `dir` query parameter name
        page: int | None = None,
    ) -> str:
        """Encode this page's sort/dir/page/filters as a query string, with optional overrides.

        Templates use this to build sort-header and pagination links without
        duplicating the query-parameter contract; callers only override the one
        axis they are changing (e.g. just ``page``, or just ``sort``/``dir``).
        """
        params: dict[str, str] = {
            'sort': sort if sort is not None else self.sort,
            'dir': dir if dir is not None else self.dir,
            'page': str(page if page is not None else self.page),
        }
        params.update(self.filters)
        return urlencode(params)
```

- [ ] **Step 6: Implement `libs/web_tables/__init__.py`**

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic server-side table query parsing and paginated list-page DTOs.

Django-free so both ``apps.web`` views and domain ``apps.*.services.queries``
modules can share one filter/sort/pagination contract without a layering
violation (domain apps must not import ``apps.web``).
"""

from __future__ import annotations

from libs.web_tables.list_page import ListPage, clamp_page
from libs.web_tables.query import TableQuery, parse_table_query
from libs.web_tables.schema import SortDir, TableSchema

__all__ = [
    'ListPage',
    'SortDir',
    'TableQuery',
    'TableSchema',
    'clamp_page',
    'parse_table_query',
]
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/libs/web_tables/tests/ -v
```

Expected: PASS

- [ ] **Step 8: Commit and sync (PR-ready chunk)**

```bash
git add backend/libs/web_tables
git commit -m "feat: add libs.web_tables generic table query/pagination contract"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 4: Queue queries — status counts, source ids, and the items page query

**Files:**
- Modify: `backend/apps/queues/services/queries.py`
- Test: `backend/apps/queues/tests/test_queries.py`

- [ ] **Step 1: Write the failing tests**

Update the imports at the top of `backend/apps/queues/tests/test_queries.py`:

```python
from apps.queues.models import Queue, QueueItemAttemptOutcome, QueueItemStatus
from apps.queues.services import commands, queries
from apps.queues.tests.base import make_second_session, make_test_queue, make_test_source
from django.utils import timezone

from libs.web_tables import TableQuery
from olib.py.django.test.cases import OTransactionTestCase
```

Append these test classes to the end of the file:

```python
class TestListQueueSummaries(OTransactionTestCase):
    def test_counts_items_by_status_across_all_agent_queues(self) -> None:
        queue_a, session = make_test_queue(identifier='summary-agent', queue_id='alpha')
        queue_b = Queue.objects.create(agent=queue_a.agent, queue_id='beta', agent_config=queue_a.agent_config)
        commands.put_item(queue=queue_a, payload={'n': 1})
        taken = commands.put_item(queue=queue_a, payload={'n': 2})
        commands.take_item(queue=queue_a, session_id=session.id)
        commands.put_item(queue=queue_b, payload={'n': 3})

        summaries = queries.list_queue_summaries(agent=queue_a.agent)

        self.assertEqual([s.queue.queue_id for s in summaries], ['alpha', 'beta'])
        alpha = summaries[0]
        self.assertEqual(alpha.counts['available'], 1)
        self.assertEqual(alpha.counts['taken'], 1)
        self.assertEqual(alpha.counts['done'], 0)
        self.assertEqual(alpha.total, 2)
        beta = summaries[1]
        self.assertEqual(beta.counts['available'], 1)
        self.assertEqual(beta.total, 1)
        del taken

    def test_returns_zeroed_counts_for_empty_queue(self) -> None:
        queue, _session = make_test_queue(identifier='summary-empty-agent')
        summaries = queries.list_queue_summaries(agent=queue.agent)
        self.assertEqual(summaries[0].total, 0)
        self.assertEqual(set(summaries[0].counts.values()), {0})


class TestListSourceIds(OTransactionTestCase):
    def test_lists_ordered_source_ids_for_queue(self) -> None:
        queue, _session = make_test_queue(identifier='source-ids-agent')
        make_test_source(queue, source_id='zeta')
        make_test_source(queue, source_id='alpha')
        self.assertEqual(queries.list_source_ids(queue=queue), ['alpha', 'zeta'])


class TestListQueueItemsPage(OTransactionTestCase):
    def _query(self, **overrides: object) -> TableQuery:
        """Build a TableQuery matching QUEUE_ITEMS_TABLE_SCHEMA, with overridable defaults."""
        defaults: dict[str, object] = {'sort': 'created_at', 'dir': 'desc', 'page': 1, 'filters': {}}
        defaults.update(overrides)
        return TableQuery(**defaults)  # type: ignore[arg-type]

    def test_default_sort_is_created_at_desc(self) -> None:
        queue, _session = make_test_queue(identifier='page-order-agent')
        first = commands.put_item(queue=queue, payload={'n': 1})
        second = commands.put_item(queue=queue, payload={'n': 2})
        page = queries.list_queue_items_page(queue=queue, query=self._query())
        self.assertEqual([item.id for item in page.rows], [second.item_id, first.item_id])

    def test_includes_terminal_statuses(self) -> None:
        queue, session = make_test_queue(identifier='page-terminal-agent')
        put_result = commands.put_item(queue=queue, payload={'n': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        page = queries.list_queue_items_page(queue=queue, query=self._query())

        self.assertEqual({item.id for item in page.rows}, {put_result.item_id})
        self.assertEqual(page.rows[0].status, QueueItemStatus.DONE)

    def test_filters_by_status(self) -> None:
        queue, session = make_test_queue(identifier='page-status-agent')
        available = commands.put_item(queue=queue, payload={'n': 1})
        commands.put_item(queue=queue, payload={'n': 2})
        commands.take_item(queue=queue, session_id=session.id)

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'status': 'available'}))

        self.assertEqual({item.id for item in page.rows}, {available.item_id})

    def test_filters_by_source(self) -> None:
        queue, _session = make_test_queue(identifier='page-source-agent')
        source_a = make_test_source(queue, source_id='src-a')
        source_b = make_test_source(queue, source_id='src-b')
        item_a = commands.put_item(queue=queue, payload={'n': 1}, source=source_a, external_id='a-1')
        commands.put_item(queue=queue, payload={'n': 2}, source=source_b, external_id='b-1')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'source': 'src-a'}))

        self.assertEqual({item.id for item in page.rows}, {item_a.item_id})

    def test_search_matches_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='page-q-external-agent')
        source = make_test_source(queue)
        match = commands.put_item(queue=queue, payload={'n': 1}, source=source, external_id='task-alpha')
        commands.put_item(queue=queue, payload={'n': 2}, source=source, external_id='task-beta')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'alpha'}))

        self.assertEqual({item.id for item in page.rows}, {match.item_id})

    def test_search_matches_failure_reason(self) -> None:
        queue, session = make_test_queue(identifier='page-q-failure-agent')
        put_result = commands.put_item(queue=queue, payload={'n': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='rate limited upstream')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'rate limited'}))

        self.assertEqual({item.id for item in page.rows}, {put_result.item_id})

    def test_search_matches_payload_text(self) -> None:
        queue, _session = make_test_queue(identifier='page-q-payload-agent')
        match = commands.put_item(queue=queue, payload={'note': 'needs-triage'})
        commands.put_item(queue=queue, payload={'note': 'routine'})

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'needs-triage'}))

        self.assertEqual({item.id for item in page.rows}, {match.item_id})

    def test_page_clamps_to_last_page_when_out_of_range(self) -> None:
        queue, _session = make_test_queue(identifier='page-clamp-agent')
        commands.put_item(queue=queue, payload={'n': 1})

        page = queries.list_queue_items_page(queue=queue, query=self._query(page=99))

        self.assertEqual(page.page, 1)
        self.assertEqual(page.total_pages, 1)
        self.assertEqual(len(page.rows), 1)

    def test_page_defaults_to_one_when_total_is_zero(self) -> None:
        queue, _session = make_test_queue(identifier='page-empty-agent')

        page = queries.list_queue_items_page(queue=queue, query=self._query())

        self.assertEqual(page.page, 1)
        self.assertEqual(page.total, 0)
        self.assertEqual(list(page.rows), [])

    def test_sorts_ascending_when_requested(self) -> None:
        queue, _session = make_test_queue(identifier='page-asc-agent')
        first = commands.put_item(queue=queue, payload={'n': 1})
        second = commands.put_item(queue=queue, payload={'n': 2})

        page = queries.list_queue_items_page(queue=queue, query=self._query(sort='created_at', dir='asc'))

        self.assertEqual([item.id for item in page.rows], [first.item_id, second.item_id])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/queues/tests/test_queries.py -v
```

Expected: FAIL — `AttributeError: module 'apps.queues.services.queries' has no attribute 'list_queue_summaries'` (and similarly for `list_source_ids`, `list_queue_items_page`).

- [ ] **Step 3: Implement the new queries**

Replace the imports at the top of `backend/apps/queues/services/queries.py`:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only queue domain access."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from apps.agents.models import Agent
from apps.queues.models import Queue, QueueItem, QueueItemAttempt, QueueItemStatus, Source
from django.db.models import Count, Q, TextField
from django.db.models.functions import Cast, Left

from libs.web_tables import ListPage, TableQuery, TableSchema, clamp_page
```

Append the following to the end of the file (after `list_attempts_for_item`):

```python
PAYLOAD_SEARCH_TEXT_CAP = 2000
"""Character cap on the payload JSON text scanned by the ``q`` filter (bounded string form)."""

QUEUE_ITEMS_SORT_FIELDS: dict[str, str] = {
    'status': 'status',
    'created_at': 'created_at',
    'external_id': 'external_id',
    'source': 'source__source_id',
    'attempt_count': 'attempt_count',
    # Sorts by the taker session's id, not a display name — there is no stable
    # human-readable session label available at the query layer.
    'taken_by': 'taken_by_session_id',
    'taken_at': 'taken_at',
    'completed_at': 'completed_at',
}

QUEUE_ITEMS_TABLE_SCHEMA = TableSchema(
    sort_keys=frozenset(QUEUE_ITEMS_SORT_FIELDS),
    default_sort='created_at',
    default_dir='desc',
    page_size=50,
    filter_keys=frozenset({'status', 'source', 'q'}),
)


@dataclass(frozen=True, slots=True)
class QueueSummary:
    """One agent-owned queue plus its per-status item counts, for the agent detail Queues section."""

    queue: Queue
    counts: dict[str, int]
    total: int


def list_queue_summaries(*, agent: Agent) -> list[QueueSummary]:
    """List *agent*'s queues (ordered by slug) with per-status item counts.

    Uses one grouped aggregate query across every one of the agent's queues,
    rather than one COUNT per queue, so the Queues section costs a constant
    number of queries regardless of how many queues the agent has.
    """
    agent_queues = list_queues(agent=agent)
    counts_by_queue: dict[UUID, dict[str, int]] = {
        queue.id: dict.fromkeys(QueueItemStatus.values, 0) for queue in agent_queues
    }
    rows = QueueItem.objects.filter(queue__agent=agent).values('queue_id', 'status').annotate(count=Count('id'))
    for row in rows:
        counts_by_queue.setdefault(row['queue_id'], dict.fromkeys(QueueItemStatus.values, 0))
        counts_by_queue[row['queue_id']][row['status']] = row['count']
    return [
        QueueSummary(
            queue=queue,
            counts=counts_by_queue[queue.id],
            total=sum(counts_by_queue[queue.id].values()),
        )
        for queue in agent_queues
    ]


def list_source_ids(*, queue: Queue) -> list[str]:
    """Return *queue*'s source ids, ordered, for the items-page source filter options."""
    return list(Source.objects.filter(queue=queue).order_by('source_id').values_list('source_id', flat=True))


def list_queue_items_page(*, queue: Queue, query: TableQuery) -> ListPage[QueueItem]:
    """Return one filtered/sorted/paginated page of *queue*'s items, all statuses included.

    Assumes *query* was built via ``parse_table_query(params, QUEUE_ITEMS_TABLE_SCHEMA)``,
    so ``query.sort`` is already guaranteed to be a key of ``QUEUE_ITEMS_SORT_FIELDS``.
    """
    qs = QueueItem.objects.filter(queue=queue).select_related('source', 'taken_by_session')

    status_filter = query.filters.get('status', '')
    if status_filter in QueueItemStatus.values:
        qs = qs.filter(status=status_filter)

    source_filter = query.filters.get('source', '')
    if source_filter:
        qs = qs.filter(source__source_id=source_filter)

    q_filter = query.filters.get('q', '')
    if q_filter:
        qs = qs.annotate(
            payload_text=Left(Cast('payload', output_field=TextField()), PAYLOAD_SEARCH_TEXT_CAP),
        ).filter(
            Q(external_id__icontains=q_filter)
            | Q(failure_reason__icontains=q_filter)
            | Q(payload_text__icontains=q_filter),
        )

    order_field = QUEUE_ITEMS_SORT_FIELDS[query.sort]
    ordering = order_field if query.dir == 'asc' else f'-{order_field}'
    qs = qs.order_by(ordering, 'id')

    total = qs.count()
    total_pages = 1 if total <= 0 else -(-total // QUEUE_ITEMS_TABLE_SCHEMA.page_size)
    page = clamp_page(query.page, total_pages)
    start = (page - 1) * QUEUE_ITEMS_TABLE_SCHEMA.page_size
    end = start + QUEUE_ITEMS_TABLE_SCHEMA.page_size
    rows = list(qs[start:end])

    return ListPage(
        rows=rows,
        total=total,
        page=page,
        page_size=QUEUE_ITEMS_TABLE_SCHEMA.page_size,
        sort=query.sort,
        dir=query.dir,
        filters=query.filters,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/apps/queues/tests/test_queries.py -v
```

Expected: PASS

- [ ] **Step 5: Run the full queues suite to catch regressions**

```bash
./olib/scripts/orunr py test backend/apps/queues/ -v
```

Expected: PASS

- [ ] **Step 6: Commit and sync (PR-ready chunk)**

```bash
git add backend/apps/queues/services/queries.py backend/apps/queues/tests/test_queries.py
git commit -m "feat: add queue summaries and a filtered/sorted/paginated items page query"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 5: Queue commands — publish scoped `queues` resource hints

**Files:**
- Modify: `backend/apps/queues/services/commands.py`
- Test: `backend/apps/queues/tests/test_commands.py`

- [ ] **Step 1: Write the failing tests**

Add imports to the top of `backend/apps/queues/tests/test_commands.py`:

```python
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from apps.queues.exceptions import (
    QueueItemNotFoundError,
    QueueItemStateError,
    QueueNotTakerError,
    QueuePayloadTooLargeError,
    QueueValidationError,
)
from apps.queues.models import (
    QueueItem,
    QueueItemAttempt,
    QueueItemAttemptOutcome,
    QueueItemStatus,
)
from apps.queues.services import commands
from apps.queues.tests.base import (
    make_second_session,
    make_test_queue,
    make_test_source,
)
from django.db import IntegrityError
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase
```

(`json` and `timedelta`/`timezone` are new; keep the rest as-is if already present.)

Append this test class to the end of the file:

```python
class TestQueueResourceHints(OTransactionTestCase):
    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_put_item_publishes_scoped_hint_for_new_item(self, publish: MagicMock) -> None:
        queue, _session = make_test_queue(identifier='hint-put-agent')

        commands.put_item(queue=queue, payload={'x': 1})

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_take_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-take-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        publish.reset_mock()

        commands.take_item(queue=queue, session_id=session.id)

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_complete_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-complete-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        publish.reset_mock()

        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_fail_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-fail-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        publish.reset_mock()

        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='bad')

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_release_stale_items_publishes_hint_for_released_item(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-release-agent')
        put_result = commands.put_item(queue=queue, payload={'x': 1})
        commands.take_item(queue=queue, session_id=session.id)
        item = QueueItem.objects.get(pk=put_result.item_id)
        item.taken_at = timezone.now() - timedelta(seconds=queue.long_hold_seconds + 1)
        item.save(update_fields=['taken_at'])
        publish.reset_mock()

        commands.release_stale_items()

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_sync_from_spec_publishes_agent_scoped_hint(self, publish: MagicMock) -> None:
        # A queue-unscoped hint is expected even when reconciliation only removes
        # orphan queues, since the Queues section still needs a refetch.
        queue, _session = make_test_queue(identifier='hint-sync-agent')
        agent = queue.agent
        config = queue.agent_config
        assert config is not None
        publish.reset_mock()

        commands.sync_from_spec(agent, config, [])

        publish.assert_called_once_with(agent.user_id, 'queues', agent_id=agent.id)
```

(`json` import above is unused by these specific tests but is needed by the existing `TestPutItem.test_put_accepts_payload_at_limit`-style tests already in the file — leave the existing `import json` line as-is if already present; do not duplicate it.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/queues/tests/test_commands.py -v
```

Expected: FAIL — `ModuleNotFoundError`/`AttributeError` style failures do not apply here; instead these `assert_called_once_with` assertions fail because `publish_resource_update_after_commit` is never called (the patched mock records zero calls).

- [ ] **Step 3: Wire scoped hints into `apps/queues/services/commands.py`**

Add the import (insert alphabetically, right after the `from __future__ import annotations` stdlib block and before `from apps.agents.models import ...`):

```python
from apps.agents.models import Agent, AgentConfig
from apps.bus.resources import publish_resource_update_after_commit
from apps.queues.exceptions import (
```

Add two helpers right after `_schedule_queue_dispatch_on_commit`:

```python
def _resolve_agent_user_id(agent_id: UUID) -> int | None:
    """Look up the owning user id for *agent_id* without loading unrelated Agent fields."""
    return Agent.objects.filter(pk=agent_id).values_list('user_id', flat=True).first()


def _publish_queue_item_hint(*, queue_id: UUID, agent_id: UUID) -> None:
    """Publish a best-effort ``queues`` hint scoped to one agent+queue, after commit."""
    user_id = _resolve_agent_user_id(agent_id)
    if user_id is None:
        return
    publish_resource_update_after_commit(user_id, 'queues', agent_id=agent_id, queue_id=queue_id)


def _publish_agent_queues_hint(*, agent_id: UUID) -> None:
    """Publish a best-effort agent-scoped (queue-unscoped) ``queues`` hint, after commit.

    Used when a whole agent's queue set may have changed shape (e.g. spec sync),
    rather than one specific queue's items.
    """
    user_id = _resolve_agent_user_id(agent_id)
    if user_id is None:
        return
    publish_resource_update_after_commit(user_id, 'queues', agent_id=agent_id)
```

In `put_item`, add a hint call next to each existing `_schedule_queue_dispatch_on_commit(queue.id)` call (three call sites: the deduped-existing-available branch, the `IntegrityError` race branch, and the created branch). For example, the created branch becomes:

```python
        _schedule_queue_dispatch_on_commit(queue.id)
        _publish_queue_item_hint(queue_id=queue.id, agent_id=queue.agent_id)
        return PutResult(item_id=item.id, created=True)
```

Apply the same one-line addition immediately after the other two `_schedule_queue_dispatch_on_commit(queue.id)` calls in `put_item` (the dedupe-hit branch and the `IntegrityError` race branch), and after the final `_schedule_queue_dispatch_on_commit(queue.id)` / `return PutResult(item_id=item.id, created=True)` pair at the end of the function (the no-source branch).

In `take_item`, add the hint call right before the final `return`:

```python
        QueueItemAttempt.objects.create(
            item=item,
            session_id=session_id,
            attempt_number=next_attempt,
            outcome=QueueItemAttemptOutcome.IN_PROGRESS,
            started_at=now,
        )
        _publish_queue_item_hint(queue_id=queue.id, agent_id=queue.agent_id)
        return TakeResult(
            item_id=item.id,
            payload=item.payload,
            attempt_count=next_attempt,
        )
```

In `_get_taken_item`, add `select_related('queue')` so `complete_item`/`fail_item` can read `item.queue.agent_id` without an extra query:

```python
def _get_taken_item(*, item_id: UUID, session_id: UUID) -> QueueItem:
    """Load a taken item (with its queue) and verify *session_id* is the current taker."""
    try:
        item = QueueItem.objects.select_related('queue').get(pk=item_id)
    except QueueItem.DoesNotExist as exc:
        raise QueueItemNotFoundError(f'queue item not found: {item_id}') from exc

    if item.status != QueueItemStatus.TAKEN:
        raise QueueItemStateError(f'item {item_id} is not taken')
    if item.taken_by_session_id != session_id:
        raise QueueNotTakerError(f'session {session_id} is not the taker for item {item_id}')
    return item
```

In `complete_item`, add the hint call after `_close_open_attempt(...)`:

```python
    _close_open_attempt(
        item=item,
        session_id=session_id,
        outcome=QueueItemAttemptOutcome.COMPLETED,
    )
    _publish_queue_item_hint(queue_id=item.queue_id, agent_id=item.queue.agent_id)
```

In `fail_item`, add the hint call after its `_close_open_attempt(...)`:

```python
    _close_open_attempt(
        item=item,
        session_id=session_id,
        outcome=QueueItemAttemptOutcome.FAILED,
        detail=reason or None,
    )
    _publish_queue_item_hint(queue_id=item.queue_id, agent_id=item.queue.agent_id)
```

In `release_stale_items`, add the hint call inside the atomic block, right after `result = _release_taken_item(...)`:

```python
                result = _release_taken_item(item=item, now=now, detail=detail)
                _publish_queue_item_hint(queue_id=item.queue_id, agent_id=item.queue.agent_id)
```

In `sync_from_spec`, add the hint call at the very end, after `_remove_orphan_queues(...)`:

```python
    _remove_orphan_queues(agent, kept_queue_ids)
    _publish_agent_queues_hint(agent_id=agent.id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/apps/queues/tests/test_commands.py -v
```

Expected: PASS

- [ ] **Step 5: Run the full queues suite to catch regressions**

```bash
./olib/scripts/orunr py test backend/apps/queues/ -v
```

Expected: PASS

- [ ] **Step 6: Commit and sync (PR-ready chunk)**

```bash
git add backend/apps/queues/services/commands.py backend/apps/queues/tests/test_commands.py
git commit -m "feat: publish scoped queues resource hints from queue commands"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 6: Web views and URLs — agent Queues partial + queue items page/partial

**Files:**
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Create: `backend/apps/web/tests/test_queue_items.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/web/tests/test_queue_items.py`:

```python
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the agent Queues section and the queue items page/partial views."""

from __future__ import annotations

import logging

from apps.agents.services.config_commands import create_from_example
from apps.queues.services import commands
from apps.queues.tests.base import make_test_queue, make_test_source
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestAgentQueuesPartial(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_requires_login(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-auth-agent')
        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))
        self.assertEqual(response.status_code, 302)

    def test_lists_owned_queue_with_counts_and_link(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-list-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'n': 1})
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))

        self.assertContains(response, 'inbox')
        self.assertContains(
            response,
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
        )

    def test_shows_empty_state_without_queues(self) -> None:
        user = get_user_model().objects.create_user(username='queues-partial-empty-user', password='test')
        agent = create_from_example(user, 'clock-assistant', identifier='queues-partial-empty-agent')
        self.client.force_login(user)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': agent.id}))

        self.assertContains(response, 'No queues configured')

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/partials/queues/$', count=1)]
    )
    def test_rejects_foreign_agent(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-foreign-agent')
        other = get_user_model().objects.create_user(username='queues-partial-foreign-user', password='test')
        self.client.force_login(other)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))

        self.assertEqual(response.status_code, 404)


class TestAgentDetailQueuesSection(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_agent_detail_includes_queues_section(self) -> None:
        queue, _session = make_test_queue(identifier='detail-queues-agent', queue_id='inbox')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': queue.agent_id}))

        self.assertContains(response, 'Queues')
        self.assertContains(response, 'inbox')
        self.assertContains(response, 'id="agent-queues"')


class TestQueueItemsView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_requires_login(self) -> None:
        queue, _session = make_test_queue(identifier='items-auth-agent', queue_id='inbox')
        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))
        self.assertEqual(response.status_code, 302)

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/queues/inbox/$', count=1)]
    )
    def test_rejects_foreign_agent(self) -> None:
        queue, _session = make_test_queue(identifier='items-foreign-agent', queue_id='inbox')
        other = get_user_model().objects.create_user(username='items-foreign-user', password='test')
        self.client.force_login(other)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertEqual(response.status_code, 404)

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/queues/missing/$', count=1)]
    )
    def test_unknown_queue_slug_returns_not_found(self) -> None:
        queue, _session = make_test_queue(identifier='items-missing-agent', queue_id='inbox')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'missing'}))

        self.assertEqual(response.status_code, 404)

    def test_renders_all_statuses_and_columns(self) -> None:
        queue, session = make_test_queue(identifier='items-render-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'available-one'})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='bad input')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'available-one')
        self.assertContains(response, 'bad input')
        self.assertContains(response, 'id="queue-items-table"')

    def test_filters_by_status_via_query_param(self) -> None:
        queue, session = make_test_queue(identifier='items-filter-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'stays-available'})
        commands.put_item(queue=queue, payload={'note': 'gets-taken'})
        commands.take_item(queue=queue, session_id=session.id)
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'status': 'taken'},
        )

        self.assertContains(response, 'gets-taken')
        self.assertNotContains(response, 'stays-available')

    def test_invalid_sort_param_falls_back_without_failing(self) -> None:
        queue, _session = make_test_queue(identifier='items-invalid-sort-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'x'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'sort': 'not-a-real-column'},
        )

        self.assertEqual(response.status_code, 200)

    def test_page_out_of_range_clamps_instead_of_failing(self) -> None:
        queue, _session = make_test_queue(identifier='items-page-clamp-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'only-item'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'page': '99'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only-item')

    def test_source_dropdown_lists_queue_sources(self) -> None:
        queue, _session = make_test_queue(identifier='items-source-options-agent', queue_id='inbox')
        make_test_source(queue, source_id='gmail-main')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertContains(response, 'gmail-main')


class TestQueueItemsPartial(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_returns_only_table_fragment(self) -> None:
        queue, _session = make_test_queue(identifier='items-partial-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'fragment-item'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items_partial', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
        )

        self.assertContains(response, 'fragment-item')
        self.assertNotContains(response, 'id="queue-items-table"')
        self.assertNotContains(response, 'frame-header')

    def test_partial_honors_current_query_string(self) -> None:
        queue, session = make_test_queue(identifier='items-partial-filter-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'stays-available'})
        commands.put_item(queue=queue, payload={'note': 'gets-taken'})
        commands.take_item(queue=queue, session_id=session.id)
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items_partial', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'status': 'available'},
        )

        self.assertContains(response, 'stays-available')
        self.assertNotContains(response, 'gets-taken')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_queue_items.py -v
```

Expected: FAIL — `NoReverseMatch: Reverse for 'agent_queues_partial' not found` (routes/views do not exist yet).

- [ ] **Step 3: Add the views in `apps/web/views.py`**

Add imports (insert alongside the existing `apps.*` import block, keeping alphabetical order):

```python
from apps.queues.models import Queue, QueueItem, QueueItemStatus
from apps.queues.services.queries import (
    QUEUE_ITEMS_TABLE_SCHEMA,
    get_queue,
    list_queue_items_page,
    list_queue_summaries,
    list_source_ids,
)
```

and:

```python
from libs.web_tables import ListPage, parse_table_query
```

Update `agent_detail` to include queue summaries:

```python
@login_required(login_url='/admin/login/')
@require_GET
def agent_detail(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Agent overview with session list, queues section, and chat input."""
    data = get_agent_detail_data(_require_authenticated_user_id(request), agent_id)
    context: dict[str, Any] = {
        'agent': data.agent,
        'sessions': data.sessions,
        'queue_summaries': list_queue_summaries(agent=data.agent),
        'source_label': data.source_label,
        'config_dirty': data.config_dirty,
        'agent_daily_spend': agent_daily_spend(data.agent.pk),
        'agent_monthly_spend': agent_monthly_spend(data.agent.pk),
        'agent_daily_limit': data.agent.daily_spend_limit_usd,
        'agent_monthly_limit': data.agent.monthly_spend_limit_usd,
    }
    context.update(_chatbox_context(agent=data.agent, session=None))
    return render(request, 'web/agent_detail.html', context)
```

Add new view functions right after `agent_detail`:

```python
def _owned_queue(agent: Agent, queue_id: str) -> Queue:
    """Return *agent*'s queue with slug *queue_id*, or raise Http404 when missing."""
    queue = get_queue(agent=agent, queue_id=queue_id)
    if queue is None:
        raise Http404('Queue not found')
    return queue


def _queue_items_page_for_request(
    request: HttpRequest,
    agent_id: UUID,
    queue_id: str,
) -> tuple[Agent, Queue, ListPage[QueueItem]]:
    """Load the owned agent/queue and one filtered/sorted/paginated items page from the request."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    queue = _owned_queue(agent, queue_id)
    query = parse_table_query(request.GET, QUEUE_ITEMS_TABLE_SCHEMA)
    list_page = list_queue_items_page(queue=queue, query=query)
    return agent, queue, list_page


@login_required(login_url='/admin/login/')
@require_GET
def agent_queues_partial(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Render the owned agent's Queues section fragment (per-status counts + links)."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    return render(
        request,
        'web/partials/agent_queues.html',
        {'agent': agent, 'queue_summaries': list_queue_summaries(agent=agent)},
    )


@login_required(login_url='/admin/login/')
@require_GET
def queue_items(request: HttpRequest, agent_id: UUID, queue_id: str) -> HttpResponse:
    """Full queue items page: agent frame chrome plus the filter/sort/pagination table."""
    agent, queue, list_page = _queue_items_page_for_request(request, agent_id, queue_id)
    context: dict[str, Any] = {
        'agent': agent,
        'queue': queue,
        'list_page': list_page,
        'status_choices': QueueItemStatus.choices,
        'source_ids': list_source_ids(queue=queue),
    }
    context.update(_chatbox_context(agent=agent, session=None))
    return render(request, 'web/queue_items.html', context)


@login_required(login_url='/admin/login/')
@require_GET
def queue_items_partial(request: HttpRequest, agent_id: UUID, queue_id: str) -> HttpResponse:
    """Render only the queue items table region, for the initial embed and htmx refetch."""
    agent, queue, list_page = _queue_items_page_for_request(request, agent_id, queue_id)
    return render(
        request,
        'web/partials/queue_items_table.html',
        {'agent': agent, 'queue': queue, 'list_page': list_page},
    )
```

- [ ] **Step 4: Add the routes in `apps/web/urls.py`**

Insert after the `start_agent_session` path and before `debug/sse-spike/`:

```python
    path('agents/<uuid:agent_id>/start/', views.start_agent_session, name='start_agent_session'),
    path('agents/<uuid:agent_id>/partials/queues/', views.agent_queues_partial, name='agent_queues_partial'),
    path('agents/<uuid:agent_id>/queues/<slug:queue_id>/', views.queue_items, name='queue_items'),
    path(
        'agents/<uuid:agent_id>/queues/<slug:queue_id>/partials/items/',
        views.queue_items_partial,
        name='queue_items_partial',
    ),
    path('debug/sse-spike/', views.sse_spike, name='sse_spike'),
```

- [ ] **Step 5: Run tests to verify they pass**

These will still fail until Task 7 adds the templates; run them anyway to confirm routing/view-layer errors are resolved and only `TemplateDoesNotExist` remains:

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_queue_items.py -v
```

Expected: FAIL with `TemplateDoesNotExist: web/queue_items.html` (or similar) — views/URLs/services are wired correctly; only templates are missing. This is expected at this point in the plan.

- [ ] **Step 6: Commit and sync (PR-ready chunk)**

```bash
git add backend/apps/web/views.py backend/apps/web/urls.py backend/apps/web/tests/test_queue_items.py
git commit -m "feat: add agent queues partial and queue items page/partial views"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 7: Templates — macros, partials, and the queue items page

**Files:**
- Create: `backend/templates/web/macros/table.html`
- Create: `backend/templates/web/partials/agent_queues.html`
- Create: `backend/templates/web/partials/queue_items_table.html`
- Create: `backend/templates/web/queue_items.html`
- Modify: `backend/templates/web/agent_detail.html`
- Modify: `backend/templates/web/partials/agent_frame_styles.html`
- Modify: `backend/templates/web/base.html` (pill colors only)
- Test: `backend/apps/web/tests/test_queue_items.py` (already written in Task 6 — no new tests needed here)

- [ ] **Step 1: Create `templates/web/macros/table.html`**

```jinja
{#
  Generic filterable-table primitive: filter-form shell, sortable column links, and
  pagination controls. Shared across list screens that render a
  libs.web_tables.list_page.ListPage. This file renders no domain-specific columns
  or row cells — callers supply those in their own template.
#}

{% macro sort_link(base_url, list_page, key, label) %}
{% set is_current = list_page.sort == key %}
{% set next_dir = 'asc' if (is_current and list_page.dir == 'desc') else 'desc' %}
<a class="table-sort-link{% if is_current %} active{% endif %}"
   href="{{ base_url }}?{{ list_page.query_string(sort=key, dir=next_dir, page=1) }}">{{ label }}{% if is_current %} {{ '▲' if list_page.dir == 'asc' else '▼' }}{% endif %}</a>
{% endmacro %}

{% macro pagination(base_url, list_page) %}
<nav class="table-pagination">
  <span class="muted">
    {% if list_page.total %}Showing {{ list_page.start_index }}–{{ list_page.end_index }} of {{ list_page.total }}{% else %}No items{% endif %}
  </span>
  <div class="table-pagination-links">
    {% if list_page.has_previous %}<a href="{{ base_url }}?{{ list_page.query_string(page=list_page.page - 1) }}">Previous</a>{% endif %}
    <span class="muted">Page {{ list_page.page }} of {{ list_page.total_pages }}</span>
    {% if list_page.has_next %}<a href="{{ base_url }}?{{ list_page.query_string(page=list_page.page + 1) }}">Next</a>{% endif %}
  </div>
</nav>
{% endmacro %}

{% macro filter_form_open(action_url, list_page) %}
<form method="get" action="{{ action_url }}" class="table-filter-form">
  <input type="hidden" name="sort" value="{{ list_page.sort }}">
  <input type="hidden" name="dir" value="{{ list_page.dir }}">
{% endmacro %}

{% macro filter_form_close() %}
  <button type="submit" class="frame-btn">Apply</button>
</form>
{% endmacro %}
```

- [ ] **Step 2: Create `templates/web/partials/agent_queues.html`**

```jinja
<h2>Queues</h2>
{% if queue_summaries %}
<table>
  <thead>
    <tr>
      <th>Queue</th>
      <th>Available</th>
      <th>Taken</th>
      <th>Done</th>
      <th>Failed</th>
      <th>Exhausted</th>
      <th>Total</th>
    </tr>
  </thead>
  <tbody>
    {% for summary in queue_summaries %}
    <tr>
      <td>
        <a href="{{ url('queue_items', kwargs={'agent_id': agent.id, 'queue_id': summary.queue.queue_id}) }}">{{ summary.queue.queue_id }}</a>
      </td>
      <td>{{ summary.counts['available'] }}</td>
      <td>{{ summary.counts['taken'] }}</td>
      <td>{{ summary.counts['done'] }}</td>
      <td>{{ summary.counts['failed'] }}</td>
      <td>{{ summary.counts['exhausted'] }}</td>
      <td>{{ summary.total }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">No queues configured for this agent.</p>
{% endif %}
```

- [ ] **Step 3: Create `templates/web/partials/queue_items_table.html`**

```jinja
{% from "web/macros/table.html" import sort_link, pagination %}
{% set base_url = url('queue_items', kwargs={'agent_id': agent.id, 'queue_id': queue.queue_id}) %}
<table>
  <thead>
    <tr>
      <th>{{ sort_link(base_url, list_page, 'status', 'Status') }}</th>
      <th>{{ sort_link(base_url, list_page, 'created_at', 'Created') }}</th>
      <th>Payload</th>
      <th>{{ sort_link(base_url, list_page, 'external_id', 'External ID') }}</th>
      <th>{{ sort_link(base_url, list_page, 'source', 'Source') }}</th>
      <th>{{ sort_link(base_url, list_page, 'attempt_count', 'Attempts') }}</th>
      <th>{{ sort_link(base_url, list_page, 'taken_by', 'Taken by') }}</th>
      <th>{{ sort_link(base_url, list_page, 'taken_at', 'Taken at') }}</th>
      <th>{{ sort_link(base_url, list_page, 'completed_at', 'Completed at') }}</th>
      <th>Failure reason</th>
    </tr>
  </thead>
  <tbody>
    {% for item in list_page.rows %}
    <tr class="queue-item-row" data-item-id="{{ item.id }}">
      <td><span class="pill {{ item.status }}">{{ item.status }}</span></td>
      <td class="muted">{{ item.created_at.strftime('%Y-%m-%d %H:%M:%S') }}</td>
      <td>
        <button type="button" class="frame-btn muted-btn" data-expand-toggle>Expand</button>
        <div class="payload-preview">{{ item.payload|tojson|truncate(120, True) }}</div>
        <pre class="payload-detail">{{ item.payload|tojson(indent=2) }}</pre>
      </td>
      <td>{{ item.external_id or '—' }}</td>
      <td>{{ item.source.source_id if item.source else '—' }}</td>
      <td>{{ item.attempt_count }}</td>
      <td>
        {% if item.taken_by_session_id %}
        <a href="{{ url('session_detail', kwargs={'session_id': item.taken_by_session_id}) }}">{{ item.taken_by_session_id }}</a>
        {% else %}—{% endif %}
      </td>
      <td class="muted">{{ item.taken_at.strftime('%Y-%m-%d %H:%M:%S') if item.taken_at else '—' }}</td>
      <td class="muted">{{ item.completed_at.strftime('%Y-%m-%d %H:%M:%S') if item.completed_at else '—' }}</td>
      <td class="muted" title="{{ item.failure_reason or '' }}">{{ (item.failure_reason or '—')|truncate(60, True) }}</td>
    </tr>
    {% endfor %}
    {% if not list_page.rows %}
    <tr><td colspan="10" class="empty">No items match the current filters.</td></tr>
    {% endif %}
  </tbody>
</table>
{{ pagination(base_url, list_page) }}
<script>
(function () {
  var container = document.getElementById('queue-items-table');
  if (!container) { return; }
  container.addEventListener('click', function (event) {
    var toggle = event.target.closest('[data-expand-toggle]');
    if (!toggle) { return; }
    var row = toggle.closest('.queue-item-row');
    if (!row) { return; }
    var expanded = row.classList.toggle('expanded');
    container.dataset.expandedId = expanded ? row.dataset.itemId : '';
  });
  var expandedId = container.dataset.expandedId;
  if (expandedId) {
    var restoredRow = container.querySelector('.queue-item-row[data-item-id="' + expandedId + '"]');
    if (restoredRow) { restoredRow.classList.add('expanded'); }
  }
})();
</script>
```

Note: `container` (`#queue-items-table`) is the persistent htmx swap-target `<div>` defined in `queue_items.html` (Step 5) — only its children are replaced on `hx-swap="innerHTML"`, so `container.dataset.expandedId` survives across a live refetch and this script (re-executed by htmx on every swap) restores the `.expanded` class for a still-present row.

- [ ] **Step 4: Add generic table CSS to `templates/web/partials/agent_frame_styles.html`**

Append inside the existing `<style>` block, right before the closing `</style>`:

```css
  /* Filterable table primitive — filter form, sortable headers, pagination */
  .table-filter-form {
    display: flex;
    gap: 1rem;
    align-items: flex-end;
    flex-wrap: wrap;
  }
  .table-filter-form label {
    display: flex;
    flex-direction: column;
    gap: .25rem;
    font-size: .8rem;
    color: #8b93a7;
  }
  .table-filter-form select,
  .table-filter-form input[type="text"] {
    background: #0f1115;
    border: 1px solid #262a33;
    color: inherit;
    border-radius: 6px;
    padding: .35rem .5rem;
    font-size: .85rem;
  }
  .table-sort-link { color: inherit; text-decoration: none; }
  .table-sort-link:hover { color: #9ec5ff; }
  .table-sort-link.active { color: #9ec5ff; font-weight: 600; }
  .table-pagination {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-top: .75rem;
    font-size: .85rem;
  }
  .table-pagination-links { display: flex; align-items: center; gap: .75rem; }
  .table-pagination-links a { color: #9ec5ff; text-decoration: none; }
  .table-pagination-links a:hover { text-decoration: underline; }
```

- [ ] **Step 5: Create `templates/web/queue_items.html`**

```jinja
{% extends "web/layout/agent_frame_page.html" %}
{% from "web/macros/table.html" import filter_form_open, filter_form_close %}

{% block title %}{{ agent.name }} — {{ queue.queue_id }} queue — Chief{% endblock %}

{% block frame_x_data %}x-data="chatboxHelpers()" x-init="focusChatInput()"{% endblock %}

{% block frame_header %}
<div>
  <h2>{{ queue.queue_id }}</h2>
  <p class="agent-meta muted">
    <a href="{{ url('agent_detail', kwargs={'agent_id': agent.id}) }}">{{ agent.name }}</a> queue
  </p>
</div>
<div class="frame-header-actions">
  <a href="{{ url('agent_detail', kwargs={'agent_id': agent.id}) }}" class="frame-btn muted-btn" style="text-decoration:none;">Back to agent</a>
</div>
{% endblock %}

{% block frame_extra_styles %}
<style>
  .payload-preview {
    font-family: ui-monospace, monospace;
    font-size: .8rem;
    max-width: 28rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .payload-detail {
    display: none;
    margin-top: .35rem;
    max-width: 32rem;
    max-height: 16rem;
    overflow: auto;
    background: #0d1015;
    border: 1px solid #303746;
    border-radius: 6px;
    padding: .5rem;
    font-size: .78rem;
    white-space: pre-wrap;
  }
  .queue-item-row.expanded .payload-preview { display: none; }
  .queue-item-row.expanded .payload-detail { display: block; }
</style>
{% endblock %}

{% block frame_main %}
<section class="card" style="margin-bottom: 1rem;">
  {{ filter_form_open(url('queue_items', kwargs={'agent_id': agent.id, 'queue_id': queue.queue_id}), list_page) }}
  <label>Status
    <select name="status">
      <option value="">All</option>
      {% for value, display in status_choices %}
      <option value="{{ value }}" {% if list_page.filters.get('status') == value %}selected{% endif %}>{{ display }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Source
    <select name="source">
      <option value="">All</option>
      {% for source_id in source_ids %}
      <option value="{{ source_id }}" {% if list_page.filters.get('source') == source_id %}selected{% endif %}>{{ source_id }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Search
    <input type="text" name="q" value="{{ list_page.filters.get('q', '') }}" placeholder="external id, failure reason, payload">
  </label>
  {{ filter_form_close() }}
</section>

<section class="card">
  <div
    id="queue-items-table"
    hx-get="{{ url('queue_items_partial', kwargs={'agent_id': agent.id, 'queue_id': queue.queue_id}) }}?{{ list_page.query_string() }}"
    hx-trigger="chief:queues-changed[(!event.detail.agentId || event.detail.agentId === '{{ agent.id }}') && (!event.detail.queueId || event.detail.queueId === '{{ queue.id }}')] from:body delay:600ms"
    hx-swap="innerHTML"
  >
    {% include "web/partials/queue_items_table.html" %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 6: Embed the Queues section in `templates/web/agent_detail.html`**

Insert a new `<section>` between the existing Usage section and the Sessions section:

```jinja
<section
  class="card"
  id="agent-queues"
  style="margin-bottom: 1rem"
  hx-get="{{ url('agent_queues_partial', kwargs={'agent_id': agent.id}) }}"
  hx-trigger="chief:queues-changed[!event.detail.agentId || event.detail.agentId === '{{ agent.id }}'] from:body delay:600ms"
  hx-swap="innerHTML"
>
  {% include "web/partials/agent_queues.html" %}
</section>

<section class="card">
  <h2>Sessions</h2>
```

(Replace the existing `<section class="card">\n  <h2>Sessions</h2>` opening with the block above, which now precedes it with the new Queues section.)

- [ ] **Step 7: Add the three missing status pill colors to `templates/web/base.html`**

Append to the existing pill rules block:

```css
    .pill.available { background: #1d3a5f; color: #9ec5ff; }
    .pill.taken { background: #3d3a1f; color: #f0e29b; }
    .pill.exhausted { background: #4a2326; color: #ffb4b8; }
```

(`.pill.done` and `.pill.failed` already exist and are reused as-is for those two queue item statuses.)

- [ ] **Step 8: Run the full web test suite**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/ -v
```

Expected: PASS (including every test written in Task 6's `test_queue_items.py`, and no regressions in `test_agent_detail.py`, `test_resource_events.py`, `test_resource_partials.py`)

- [ ] **Step 9: Run the full project test gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 10: Commit and sync (PR-ready chunk)**

```bash
git add backend/templates/web/macros/table.html backend/templates/web/partials/agent_queues.html backend/templates/web/partials/queue_items_table.html backend/templates/web/queue_items.html backend/templates/web/agent_detail.html backend/templates/web/partials/agent_frame_styles.html backend/templates/web/base.html
git commit -m "feat: add the queue items page, agent Queues section, and shared table macros"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

### Task 8: `docs/ARCHITECTURE.md` — document the new import edge and resource

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update the `apps.queues` import-direction row**

Replace:

```markdown
| `apps.queues` | Django/stdlib, `libs.sources`, `sessions` (releasable predicate only) |
```

with:

```markdown
| `apps.queues` | Django/stdlib, `libs.sources`, `sessions` (releasable predicate only), foundational `bus` publishers |
```

- [ ] **Step 2: Update the import-direction narrative paragraph**

Replace:

```markdown
Import edges point from the importer toward its dependencies. `bus` stays foundational
and domain-free: it imports no domain app, while `agents` and `keys` may import its
publisher helpers. `keys` imports only `bus` among apps. **`apps.agents`** imports
**`apps.queues`** only for config materialization (`sync_from_spec`). `local_sync` is
an outer, cross-domain reconciler that may import `agents`, `keys`, `bus`, and
`libs.file`; no domain app imports `local_sync`. `web` remains the outer HTTP
transport and must not import `resolve_*` from keys.
```

with:

```markdown
Import edges point from the importer toward its dependencies. `bus` stays foundational
and domain-free: it imports no domain app, while `agents`, `keys`, and `queues` may
import its publisher helpers. `keys` and `queues` each import only `bus` among apps
(besides `queues`'s existing `sessions` releasable-predicate import).
**`apps.agents`** imports **`apps.queues`** only for config materialization
(`sync_from_spec`). `local_sync` is an outer, cross-domain reconciler that may import
`agents`, `keys`, `bus`, and `libs.file`; no domain app imports `local_sync`. `web`
remains the outer HTTP transport and must not import `resolve_*` from keys.
```

- [ ] **Step 3: Add `libs/web_tables` to the Libraries table**

Replace:

```markdown
| `libs/sources` | Source adapter protocol + registry |
| `libs/algorithms` | Reusable algorithms (may call providers) |
```

with:

```markdown
| `libs/sources` | Source adapter protocol + registry |
| `libs/algorithms` | Reusable algorithms (may call providers) |
| `libs/web_tables` | Generic table query parsing + paginated list-page DTO (Django-free); shared by `apps.web` views and domain `services/queries.py` modules so neither imports the other |
```

- [ ] **Step 4: Document the `queues` resource and scoping fields**

Replace:

```markdown
List resources use a separate user-scoped Redis channel:
**`{CACHE_PREFIX}user:{user_id}:resources`**. Agent and key commands publish the
canonical, secret-free envelope
`{"channel": "resource_update", "resource": "agents"|"keys"}` with
`transaction.on_commit`. These messages are best-effort refetch hints only: Postgres
is authoritative, and no model data or credential material belongs in the envelope.

Authenticated pages connect to **`/events/`**, whose SSE stream derives the user id
from the session and subscribes only to that user's channel. The shared page script
keeps at most one `EventSource`, closes it on `pagehide`, and reopens it on a
BFCache-restored `pageshow`. A validated `resource_update` triggers
`chief:agents-changed` or `chief:keys-changed`; htmx then refetches
`/partials/agents/` or `/partials/keys/` and swaps the relevant list contents.
Redis pub/sub is not replayed, so clients must tolerate lost or coalesced hints:
each partial refetch reads current Postgres state, and a later hint or navigation
converges the page.
```

with:

```markdown
List resources use a separate user-scoped Redis channel:
**`{CACHE_PREFIX}user:{user_id}:resources`**. Agent, key, and queue commands publish
the canonical, secret-free envelope
`{"channel": "resource_update", "resource": "agents"|"keys"|"queues"}` with
`transaction.on_commit`. The `queues` resource additionally carries optional
`agent_id` / `queue_id` UUID-string scoping fields so a specific agent's Queues
section or one queue's items table can refetch without an unscoped page-wide
signal; both fields are omitted entirely (never sent as null) when the caller has
no scope to supply, and an unscoped `queues` hint is treated as "refetch if any
queue UI is visible". These messages are best-effort refetch hints only: Postgres
is authoritative, and no model data, filter state, or credential material belongs
in the envelope.

Authenticated pages connect to **`/events/`**, whose SSE stream derives the user id
from the session and subscribes only to that user's channel. The shared page script
keeps at most one `EventSource`, closes it on `pagehide`, and reopens it on a
BFCache-restored `pageshow`. A validated `resource_update` triggers
`chief:agents-changed`, `chief:keys-changed`, or `chief:queues-changed` (forwarding
`agent_id`/`queue_id` as the custom event's `detail.agentId`/`detail.queueId`); htmx
then refetches `/partials/agents/`, `/partials/keys/`, or the relevant agent/queue
partial and swaps the matching content. The queue items page and the agent detail
Queues section use an `hx-trigger` event-filter expression
(`chief:queues-changed[!event.detail.agentId || event.detail.agentId === '<id>']`)
combined with a `delay` trigger modifier, so only a matching scoped hint (or an
unscoped one) triggers their htmx refetch, and bursts of hints coalesce into one
request. Redis pub/sub is not replayed, so clients must tolerate lost or coalesced
hints: each partial refetch reads current Postgres state, and a later hint or
navigation converges the page.
```

- [ ] **Step 5: Cross-link this spec from the Queues & sources section**

Replace:

```markdown
**Attempt history:** when an item is retried across sessions (stale release, explicit
`fail`, worker pool), **`QueueItemAttempt`** records **every** session that took it —
not only the current taker on `QueueItem`. Operators and debug tooling can list all
sessions that tried an item before it reached `done`, `failed`, or `exhausted`.
```

with:

```markdown
**Attempt history:** when an item is retried across sessions (stale release, explicit
`fail`, worker pool), **`QueueItemAttempt`** records **every** session that took it —
not only the current taker on `QueueItem`. Operators and debug tooling can list all
sessions that tried an item before it reached `done`, `failed`, or `exhausted`.

**Queue items UI:** operators browse all items (including terminal statuses) per
queue from agent detail, with server-side filter/sort/pagination and live scoped
refresh hints — see
[`docs/specs/2026-08-02-queue-items-ui/`](specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-design.md).
```

- [ ] **Step 6: Confirm no other architecture references need updating**

```bash
rg -n "apps.queues.*Django/stdlib|resource.*agents.*keys" docs/ARCHITECTURE.md
```

Expected: only the lines just edited above appear; no stale `"agents"|"keys"` (without `queues`) references remain in the resource envelope description.

- [ ] **Step 7: Commit and sync (PR-ready chunk)**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: document apps.queues -> bus and the queues resource hint scoping"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

## Out of scope (per design non-goals)

- Migrating the existing Sessions table on agent detail to the new table primitive.
- Item mutations from the UI (requeue, force-fail, manual put) — this is a read-only browser.
- A dedicated attempt-history detail page.
- Client-side-only filter/sort of the full queue (everything stays server-side/authoritative).
- Session-style per-item SSE payloads (row merge) — hints + refetch only.
- A cross-agent / global queue dashboard.
- Any change to queue lifecycle semantics or the payload envelope shape.

---

## S_final — Code review (mandatory)

### Task 10: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

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

Read `superpowers/requesting-code-review` skill. Dispatch reviewer subagent with:

- `{DESCRIPTION}` — brief summary of what was implemented on this branch (queue items UI: scoped `queues` resource hints, `libs.web_tables` filterable-table primitive, queue queries/commands, agent Queues section, queue items page)
- `{PLAN_OR_REQUIREMENTS}` — path to `docs/specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-design.md` and `docs/specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Read `superpowers/requesting-code-review` skill and **`review-file-template.md`**.

1. Write `docs/specs/2026-08-02-queue-items-ui/2026-08-02-queue-items-ui-review.md` (same prefix as `-design.md` / `-plan.md`).
2. One issue table per severity with columns: `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**.
3. Summarize the same content in chat (assessment + tables).

Stop here unless the user asks to fix issues. Under `/ship`, the parent (not this subagent) owns fixing findings before opening the PR.

- [ ] **Step 5: Track feedback**

When the user requests fixes or rejects findings, update **Status** in `*-review.md`:

- **Fixed** — after implementing the fix
- **Rejected** — when the user declines; record rationale in **Notes**

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` (PR / merge options). Do **not** check epic/spec boxes in `-revision.md` or the epic file unless the user explicitly approves after review.

---

## References

- Design: [`2026-08-02-queue-items-ui-design.md`](2026-08-02-queue-items-ui-design.md)
- Queue domain: [`2026-07-04-sources-and-queues`](../2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-design.md)
- Architecture: [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md)
