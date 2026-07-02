# Chief — Chat names (02) Implementation Plan

Companion to `2026-07-01-chat-names-design.md`. Read the spec first for the *why*; this doc
is the *how* — exact files, signatures, ordering, and decisions locked before
coding.

**No backwards compatibility.** Rename SSE types, wrap all Redis pub/sub
messages in the new envelope, and update every call site in one pass per stage.

---

## Progress checklist

**Overall:** ✅ Backend complete — olib mypy pre-existing failure only (`lazy.py`).

### S0 — Scaffold libs

- [x] `backend/libs/__init__.py`
- [x] `backend/libs/providers/__init__.py`
- [x] `backend/libs/tools/__init__.py`
- [x] `backend/libs/algorithms/__init__.py`
- [x] `libs.*` imports resolve (smoke import)
- [x] S0 tests green

### S1 — Move providers + tools

- [x] `git mv` `apps/runner/providers` → `libs/providers`
- [x] `libs/providers/types.py` — `ProviderLLMConfig`
- [x] `apps/runner/llm_config.py` — `provider_config_from_spec`
- [x] Providers refactored off `apps.agents.spec.LLMSpec` → `ProviderLLMConfig`
- [x] `make_provider` takes `ProviderLLMConfig`
- [x] Provider imports use `libs.tools` not `apps.agents.tools`
- [x] Provider tests moved to `libs/providers/tests/`
- [x] `git mv` `apps/agents/tools` → `libs/tools`
- [x] `libs/tools/registry.py` — empty `TOOLS` + `register_tool`
- [x] `apps/agents/tools_wiring.py` + `AppConfig.ready()`
- [x] `apps/agents/tests/test_tools_wiring.py`
- [x] All `apps.runner.providers` / `apps.agents.tools` imports gone (grep)
- [x] S1 tests + mypy green

### S2 — Algorithms

- [x] `libs/algorithms/chat_name.py` — `ChatNameConfig`, `generate_chat_name`
- [x] `libs/algorithms/tests/test_chat_name.py`
- [x] S2 tests green

### S3 — Session model

- [x] `AgentSession.name` field on model
- [x] Migration generated + applied
- [x] `name` on admin `list_display`
- [x] S3 tests green

### S4 — Notifications envelope + SSE rename

- [x] `apps/sessions/notify.py`
- [x] `apps/bus/channels.py` — `publish_session_message`; remove bare `publish_event`
- [x] `apps/runner/backends/django.py` → `notify.publish_session_event`
- [x] `apps/web/views.py` — envelope parse; `session_event` / `session_update`
- [x] `session_detail.html` — `session_event` listener (not `session-event`)
- [x] Bus + SSE tests updated
- [x] S4 tests green

### S5 — Services + runner INPUT path

- [x] `apps/sessions/services/queries.py`
- [x] `apps/sessions/services/commands.py` — `record_input`, `update_session_name`
- [x] `apps/runner/backends/base.py` — `record_input` abstract
- [x] `apps/runner/backends/django.py` + `memory.py` — `record_input`
- [x] `apps/runner/loop.py` — chat uses `record_input` only (no double publish)
- [x] `apps/sessions/tests/test_services_*.py`
- [x] S5 tests green

### S6 — Celery task

- [x] `apps/sessions/tasks.py` — `generate_session_name`
- [x] `chief/tasks.py` imports `apps.sessions.tasks`
- [x] `apps/sessions/tests/test_tasks.py`
- [x] S6 tests green

### S7 — UI

- [x] `templates/web/macros/session.html` — `session_display_name`
- [x] `dashboard.html` + `agent_detail.html` use macro
- [x] `session_detail.html` — Alpine `sessionName`, `session_update`, `document.title`
- [x] `test_session_dialog.py` updated for `session_event`
- [x] S7 tests green

### Done

- [x] Full `./olib/scripts/orunr py test-all` green (backend; olib mypy pre-existing)
- [x] `./olib/scripts/orunr py lint` green (backend)
- [x] `./olib/scripts/orunr py mypy` green (backend)

### Manual

- [ ] **Live name on session page** — log in, start a chat with a distinct first
  message; header shows id prefix initially; within a few seconds header and tab
  title update to the generated name without reload; event log continues streaming
  assistant replies.
- [ ] **With `OPENAI_API_KEY`** — repeat the above against compose with a real
  cheap-model title (default `gpt-4o-mini` via `ChatNameConfig`).
- [ ] **Without API key (optional)** — dev-only check using
  `ChatNameConfig(provider='repeat')` on the task, or rely on repeat-provider unit
  tests if skipping live compose.

---

## Current status (2026-07-02)

| Stage | Status | Notes |
|-------|--------|-------|
| S0 Scaffold libs | ✅ Done | |
| S1 Move providers + tools | ✅ Done | |
| S2 Algorithms | ✅ Done | |
| S3 Session model + migration | ✅ Done | `0004_agentsession_name` |
| S4 Notifications envelope | ✅ Done | |
| S5 Services + runner INPUT path | ✅ Done | |
| S6 Celery task | ✅ Done | |
| S7 UI | ✅ Done | Automated tests green; see **Manual** checklist |

---

## Conventions (unchanged from v0.1)

- Django apps: `backend/apps/<name>/`, `AppConfig.name = 'apps.<name>'`.
- Sessions app label: `agent_sessions` (not Django's `sessions`).
- Python imports: `backend/` is on `PYTHONPATH` → `apps.*`, `chief.*`, and new
  **`libs.*`** (same as `apps.*`).
- Migrations: always via `./olib/scripts/orunr django manage makemigrations`.
- Required check after each stage: `./olib/scripts/orunr py test-all`.

Commands (repo root):

```bash
./olib/scripts/orunr py test-all
./olib/scripts/orunr py lint
./olib/scripts/orunr py mypy
./olib/scripts/orunr django manage makemigrations
./olib/scripts/orunr django manage migrate
```

---

## Target layout (after all stages)

```
backend/
  libs/
    providers/
      __init__.py
      types.py              # ProviderLLMConfig
      base.py
      spec.py
      registry.py
      openai_provider.py
      anthropic_provider.py
      local_openai_provider.py
      repeat_provider.py
      fake_provider.py
      tests/
        test_openai_provider.py
        test_repeat_provider.py
        ...
    tools/
      __init__.py
      base.py
      schema.py
      registry.py           # register_tool / get_tool; TOOLS dict starts empty
      builtin.py            # ClockTool class only (no auto-register)
      tests/
        test_schema.py
    algorithms/
      __init__.py
      chat_name.py          # ChatNameConfig, generate_chat_name
      tests/
        test_chat_name.py
  apps/
    agents/
      tools_wiring.py       # wire_tools() — registers ClockTool
      apps.py               # ready() calls wire_tools()
      spec.py               # LLMSpec unchanged
      ingest.py             # import libs.tools.registry.get_tool
    sessions/
      models.py             # + name field
      notify.py             # envelope helpers
      services/
        queries.py
        commands.py
      tasks.py
      migrations/0004_agentsession_name.py  (number may differ)
    runner/
      llm_config.py         # LLMSpec → ProviderLLMConfig (new)
      loop.py               # record_input path
      backends/
        base.py             # + record_input abstract
        django.py           # delegates to sessions.services.commands
        memory.py           # append + publish locally
    bus/
      channels.py           # publish_session_message only (wire format)
    web/
      views.py              # SSE envelope parse + session_event rename
      jinja2_env.py or macros  # session_display_name helper
  chief/
    tasks.py                # + import apps.sessions.tasks
  templates/web/
    session_detail.html     # Alpine sessionName + session_update
    dashboard.html          # display name macro
    agent_detail.html       # display name macro
    macros/session.html       # optional macro (recommended)
```

---

## Locked decisions (was open in spec)

| Topic | Decision |
|-------|----------|
| Runner publish consolidation (all event kinds) | **Deferred.** v1 only INPUT goes through `record_input` command. OUTPUT / TOOL_* / FAILURE still publish via `DjangoSessionBackend.publish_event` → `notify.publish_session_event`. |
| Manual rename | **Deferred** (no `name_source` column in v1). |
| Dashboard / agent list live name update | **Accepted limitation.** Live update only on session detail (active SSE). Lists update on reload. |
| Non-English titles | **Yes** — system prompt instructs model to match the user's message language. |
| Redis channel key suffix | Keep **`session:{id}:events`** (legacy name; carries both event + update envelopes). |
| `document.title` live update | **In v1** — Alpine watcher updates title when `sessionName` changes. |
| Admin | Add **`name`** to `AgentSessionAdmin.list_display` and readonly on detail. |
| Celery in tests | **`CELERY_WORKERS_ALWAYS_EAGER = True`** in olib test settings — `.delay()` runs inline; patch `generate_chat_name` in command tests that must not call LLM. |

---

## S0 — Scaffold `backend/libs/`

### Tasks

- [ ] Create `backend/libs/providers/__init__.py`, `libs/tools/__init__.py`,
  `libs/algorithms/__init__.py` (empty packages).
- [ ] Confirm `libs.*` imports resolve (same as `apps.*` — no `config.py` change
  expected; `PyRoot('./backend')` already covers all of `backend/`).
- [ ] `AGENTS.local.md` — already updated; no further change unless drift.

### Verify

```bash
./olib/scripts/orunr py test-all   # green, no behavior change
```

---

## S1 — Move providers + tools

### S1a. Move providers

**Action:** `git mv backend/apps/runner/providers backend/libs/providers`

**New file:** `backend/apps/runner/llm_config.py`

```python
from apps.agents.spec import LLMSpec
from libs.providers.types import ProviderLLMConfig

def provider_config_from_spec(llm: LLMSpec) -> ProviderLLMConfig:
    return ProviderLLMConfig(
        provider=llm.provider,
        model=llm.model,
        temperature=llm.temperature,
    )
```

**New file:** `backend/libs/providers/types.py`

```python
class ProviderLLMConfig(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
```

**Refactor inside `libs/providers/`:**

| File | Change |
|------|--------|
| `base.py` | Replace `from apps.agents.spec import LLMSpec` → `ProviderLLMConfig` in factory signatures |
| `registry.py` | `make_provider(llm: ProviderLLMConfig) -> LLMProvider` |
| `repeat_provider.py`, `fake_provider.py`, … | `_from_spec(..., llm: ProviderLLMConfig)` |
| `openai_provider.py`, `anthropic_provider.py` | Replace `apps.agents.tools.*` → `libs.tools.*` |

**Update imports across codebase** (grep `apps.runner.providers` and `apps.agents.tools`):

| File | New import |
|------|------------|
| `apps/runner/loop.py` | `libs.providers.*`, `libs.tools.*`, `make_provider(provider_config_from_spec(...))` |
| `apps/runner/run_agent.py` | same pattern |
| `apps/web/demo_models.py` | `libs.providers.openai_provider` etc. |
| `apps/runner/tests/*` | `libs.providers.*` |
| `apps/sessions/rebuild.py` | `libs.tools.base.qualified_tool_name` |

**Delete:** `backend/apps/runner/providers/` (after move).

**Move tests:** provider unit tests from `apps/runner/tests/test_*provider*.py` →
`libs/providers/tests/` (same test bodies, updated imports).

### S1b. Move tools

**Action:** `git mv backend/apps/agents/tools backend/libs/tools`

**Refactor `libs/tools/registry.py`:**

```python
TOOLS: dict[str, Tool] = {}

def register_tool(name: str, tool: Tool) -> None:
    TOOLS[name] = tool

def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)

def all_tools() -> dict[str, Tool]:
    return dict(TOOLS)
```

Remove eager `ClockTool()` registration from registry (moves to wiring).

**New file:** `backend/apps/agents/tools_wiring.py`

```python
def wire_tools() -> None:
    from libs.tools.builtin import ClockTool
    from libs.tools.registry import register_tool

    register_tool('clock', ClockTool())
```

**Update `apps/agents/apps.py`:**

```python
class AgentsConfig(AppConfig):
    name = 'apps.agents'

    def ready(self) -> None:
        from apps.agents.tools_wiring import wire_tools
        wire_tools()
```

**Delete:** `backend/apps/agents/tools/` (after move).

**Update `apps/agents/ingest.py`:** `from libs.tools.registry import get_tool`.

**Delete:** `apps/agents/tools/__init__.py` re-export bridge — update any
remaining `from apps.agents.tools` to `libs.tools`.

**Tests:** move tool schema tests if any; add
`apps/agents/tests/test_tools_wiring.py` asserting `get_tool('clock')` after
`django.setup()`.

### S1 verify

- [ ] `./olib/scripts/orunr py test-all`
- [ ] `./olib/scripts/orunr py mypy`
- [ ] Grep confirms zero `apps.runner.providers` and zero `apps.agents.tools`.

---

## S2 — `libs/algorithms/chat_name.py`

### New files

**`libs/algorithms/chat_name.py`**

```python
class ChatNameConfig(BaseModel):
    provider: str = 'openai'
    model: str = 'gpt-4o-mini'
    temperature: float = 0.2
    max_title_chars: int = 80
    enabled: bool = True

DEFAULT_CHAT_NAME_CONFIG = ChatNameConfig()

_SYSTEM_PROMPT = (
    'You generate short chat titles. Reply with ONLY the title, no quotes or '
    'punctuation wrapper. Use the same language as the user message. '
    'Target 3–8 words.'
)

def generate_chat_name(
    first_message: str,
    *,
    config: ChatNameConfig | None = None,
) -> str:
    ...
```

**Implementation notes:**

1. If `not cfg.enabled`, return `_fallback_title(first_message, cfg)` immediately.
2. Build `ProviderLLMConfig` from `cfg`; `make_provider(...)` from `libs.providers.registry`.
3. `provider.collect(messages, tool_definitions=[])` — no tools.
4. Sanitize model output: strip whitespace, strip surrounding `"`/`'`, collapse
   newlines, truncate to `max_title_chars`.
5. On `ProviderError`, empty content, or exception → `_fallback_title`.

**`_fallback_title`:** normalize whitespace; if empty return `'New chat'`; truncate
with `…` at `max_title_chars`.

**`libs/algorithms/tests/test_chat_name.py`**

| Test | Approach |
|------|----------|
| Disabled config | `enabled=False` → fallback, no provider call |
| Repeat provider | `ChatNameConfig(provider='repeat', model='x')` + patch env keys if needed; assert non-empty sanitized string |
| Provider failure | `FakeProvider` or mock `collect` raising → fallback |
| Truncation | Long message → fallback length ≤ max_title_chars |
| Override config | Custom `model` passed via config struct |

No env vars for algorithm tuning.

---

## S3 — Session model

### Model change

**`apps/sessions/models.py`** — add to `AgentSession`:

```python
name = models.CharField(max_length=80, null=True, blank=True, default=None)
```

**Migration:** `./olib/scripts/orunr django manage makemigrations sessions`

**`apps/sessions/admin.py`:** add `name` to `list_display`; show in readonly
fields on change form.

### Verify

- [ ] Migration applies cleanly on fresh and existing DB.

---

## S4 — Notification envelope + SSE rename

### Wire format (Redis)

All messages on `session:{id}:events` are JSON objects:

```json
{
  "channel": "session_event",
  "payload": { "id": "…", "session_id": "…", "seq": 1, "kind": "INPUT", … }
}
```

```json
{
  "channel": "session_update",
  "payload": { "name": "Password reset help" }
}
```

**No bare event dicts on Redis.** SSE replay from Postgres is unchanged (not
stored in Redis); only the live tail parses envelopes.

### `apps/sessions/notify.py` (new)

```python
SessionChannel = Literal['session_event', 'session_update']

def session_message(channel: SessionChannel, payload: dict[str, Any]) -> dict[str, Any]:
    return {'channel': channel, 'payload': payload}

def publish_session_event(session_id: UUID | str, event_dict: dict[str, Any]) -> None:
    publish_session_message(session_id, session_message('session_event', event_dict))

def publish_session_update(session_id: UUID | str, patch: dict[str, Any]) -> None:
    publish_session_message(session_id, session_message('session_update', patch))
```

### `apps/bus/channels.py`

- [ ] Add `publish_session_message(session_id, message: dict) -> None`.
- [ ] **Remove** `publish_event` (or make it raise / delete in same PR — no
  shims).

### Publisher updates

| Caller | New call |
|--------|----------|
| `apps/runner/backends/django.py` `publish_event` | `notify.publish_session_event(session_id, event.to_stream_dict(...))` |
| `apps/sessions/services/commands.py` (later) | `notify.publish_session_event` / `publish_session_update` |

### SSE — `apps/web/views.py`

**Rename default event type:**

```python
def _sse_event(data: dict[str, Any], *, event: str = 'session_event') -> str:
    return f'event: {event}\ndata: {json.dumps(data)}\n\n'
```

**Replay loop:** emit `event: session_event` with `event.to_stream_dict()` as
data (payload only, no envelope).

**Live tail loop:**

```python
raw = json.loads(message['data'])
channel = raw.get('channel')
payload = raw.get('payload', {})
if channel == 'session_event':
    if payload.get('seq', 0) <= last_seq:
        continue
    last_seq = payload['seq']
    yield _sse_event(payload, event='session_event')
elif channel == 'session_update':
    yield _sse_event(payload, event='session_update')
else:
    logger.warning('Unknown session message channel %r', channel)
```

Messages without `channel` key: **log warning and skip** (no legacy fallback).

### Tests to update/create

| File | Change |
|------|--------|
| `apps/web/tests/test_sse.py` | Assert `event: session_event` in body (not `session-event`) |
| `apps/bus/tests/test_channels.py` | Test `publish_session_message` round-trip JSON |
| `apps/runner/tests/test_tasks.py` | Patch `apps.sessions.notify.publish_session_event` instead of `bus.publish_event` |

### Frontend — `templates/web/session_detail.html`

- [ ] `session-event` → `session_event` in `addEventListener`.

### Verify

- [ ] Manual: start session, confirm live events still stream.
- [ ] `./olib/scripts/orunr py test-all`

---

## S5 — Services + runner INPUT path

### `apps/sessions/services/queries.py`

```python
def get_session_name(session_id: UUID) -> str | None:
    return AgentSession.objects.filter(pk=session_id).values_list('name', flat=True).first()

def get_first_input_text(session_id: UUID) -> str | None:
    row = (
        AgentSessionEvent.objects.filter(session_id=session_id, kind=AgentSessionEventKind.INPUT)
        .order_by('seq')
        .values_list('payload', flat=True)
        .first()
    )
    if not row:
        return None
    content = row.get('content', '')
    return content.strip() or None

def input_event_count(session_id: UUID) -> int:
    return AgentSessionEvent.objects.filter(
        session_id=session_id, kind=AgentSessionEventKind.INPUT
    ).count()
```

### `apps/sessions/services/commands.py`

```python
def record_input(session: AgentSession, content: str) -> AgentSessionEvent:
    row = append_event(session, AgentSessionEventKind.INPUT, {'content': content})
    publish_session_event(session.id, row.to_stream_dict())
    if input_event_count(session.id) == 1 and DEFAULT_CHAT_NAME_CONFIG.enabled:
        transaction.on_commit(
            lambda: generate_session_name.delay(str(session.id))
        )
    return row

def update_session_name(session_id: UUID, name: str, *, source: str = 'auto') -> bool:
    del source  # reserved for manual rename later
    normalized = _normalize_name(name)
    if not normalized:
        return False
    updated = AgentSession.objects.filter(pk=session_id, name__isnull=True).update(name=normalized)
    if updated:
        publish_session_update(session_id, {'name': normalized})
    return bool(updated)

def _normalize_name(name: str, *, max_len: int = 80) -> str:
    text = ' '.join(name.split())
    if not text:
        return ''
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + '…'
    return text
```

**Critical:** `transaction.on_commit` for `.delay()` so the INPUT row is
visible when the eager/inline task runs.

**Import cycle avoidance:** lazy-import `generate_session_name` inside
`record_input` or use `from apps.sessions import tasks` at function body.

### Runner backend changes

**`apps/runner/backends/base.py`** — add:

```python
@abstractmethod
def record_input(self, content: str) -> RecordedEvent:
    """Append an INPUT event (and publish / schedule side effects)."""
```

**`apps/runner/backends/django.py`:**

```python
def record_input(self, content: str) -> RecordedEvent:
    from apps.sessions.services.commands import record_input as record_input_cmd
    row = record_input_cmd(self._session, content)
    return _recorded_from_row(row)

def publish_event(self, event: RecordedEvent) -> None:
    publish_session_event(self._session.id, event.to_stream_dict(session_id=self._session.id))
```

**`apps/runner/backends/memory.py`:**

```python
def record_input(self, content: str) -> RecordedEvent:
    event = self.append_event(AgentSessionEventKind.INPUT, {'content': content})
    self.publish_event(event)
    return event
```

(Memory path: no Celery, no Redis — tests unchanged in behavior.)

**`apps/runner/loop.py`** `_drain_mailbox` chat branch:

```python
if action == 'chat':
    content = msg.get('content', '')
    if content:
        self.control.pending_inputs.append(content)
        self.backend.record_input(content)
        # Do NOT call publish_event here — record_input owns it
```

### Tests

**`apps/sessions/tests/test_services_commands.py`** (new)

| Test | Assert |
|------|--------|
| `record_input` first message | INPUT row, `publish_session_event` called, `generate_session_name.delay` scheduled (patch delay) |
| `record_input` second message | no second delay |
| `update_session_name` | name set once; `publish_session_update` with `{'name': …}` |
| `update_session_name` idempotent | second call no-op, no duplicate publish |

**`apps/sessions/tests/test_services_queries.py`** (new)

- `get_first_input_text` after one INPUT.

**Update `apps/runner/tests/test_loop.py`:** published events on memory backend
still work via `record_input` internal publish.

---

## S6 — Celery task

### `apps/sessions/tasks.py` (new)

```python
@shared_task(bind=True, ignore_result=True, max_retries=2)
def generate_session_name(self, session_id: str) -> None:
    uid = UUID(session_id)
    if get_session_name(uid) is not None:
        return
    text = get_first_input_text(uid)
    if text is None:
        return
    try:
        name = generate_chat_name(text, config=DEFAULT_CHAT_NAME_CONFIG)
    except Exception:
        logger.exception('Chat name generation failed for session %s', session_id)
        name = generate_chat_name(text, config=ChatNameConfig(enabled=False))
    update_session_name(uid, name)
```

On unexpected failure, fall back via `enabled=False` path (deterministic
fallback title) rather than leaving name null.

### `chief/tasks.py`

```python
import apps.runner.tasks  # noqa: F401
import apps.sessions.tasks  # noqa: F401
```

### Tests — `apps/sessions/tests/test_tasks.py`

Patch `generate_chat_name` to return `'Test title'`; call task; assert
`session.name == 'Test title'`.

With eager Celery, `record_input` integration test: patch `generate_chat_name`
→ assert end-to-end name on session after first chat POST (optional web-level
test in `test_start_session` or sessions test).

---

## S7 — UI

### Jinja macro (recommended)

**`templates/web/macros/session.html`**

```jinja
{% macro session_display_name(session) -%}
{{ session.name or session.id.hex[:8] }}
{%- endmacro %}
```

Use in `dashboard.html`, `agent_detail.html` session table links.

### Session detail

**`templates/web/session_detail.html`**

1. Title block: use display name in static fallback.
2. Header span: replace static `session.id.hex[:8]` with Alpine binding:

```html
<span class="muted" style="font-weight: normal;" x-text="displaySessionName">…</span>
```

3. **`sessionView` signature:**

```javascript
function sessionView(sessionId, initialName, configModel) {
  return {
    sessionName: initialName || '',
    get displaySessionName() {
      return this.sessionName || sessionId.replace(/-/g, '').slice(0, 8);
    },
    ...
```

4. Template init:

```jinja
{% block frame_x_data %}x-data='sessionView("{{ session.id }}", {{ (session.name or "")|tojson }}, {{ llm_label|tojson }})' x-init="init()"{% endblock %}
```

5. SSE listeners:

```javascript
this.source.addEventListener('session_event', (e) => { /* existing */ });
this.source.addEventListener('session_update', (e) => {
  const patch = JSON.parse(e.data);
  if (patch.name != null) this.sessionName = patch.name;
});
```

6. **`document.title`:** `$watch('displaySessionName', …)` or update inside
   `session_update` handler:

```javascript
document.title = `${this.displaySessionName} — Chief`;
```

### Tests

**`apps/web/tests/test_session_dialog.py`:** assert `session_event` listener
present (not `session-event`).

**Optional `apps/web/tests/test_session_name_sse.py`:** mock Redis publish of
`session_update` envelope; assert response stream contains
`event: session_update` — defer if flaky; manual check acceptable.

### Manual smoke test

1. Log in, start chat with a distinct first message.
2. Session header shows id prefix initially.
3. Within a few seconds, header (and tab title) updates to generated name without
   reload.
4. Event log continues streaming assistant replies.

---

## Complete file checklist (grep-driven)

### Create

- `backend/libs/providers/types.py`
- `backend/libs/algorithms/chat_name.py`
- `backend/libs/algorithms/tests/test_chat_name.py`
- `backend/apps/runner/llm_config.py`
- `backend/apps/agents/tools_wiring.py`
- `backend/apps/agents/tests/test_tools_wiring.py`
- `backend/apps/sessions/notify.py`
- `backend/apps/sessions/services/queries.py`
- `backend/apps/sessions/services/commands.py`
- `backend/apps/sessions/tasks.py`
- `backend/apps/sessions/tests/test_services_commands.py`
- `backend/apps/sessions/tests/test_services_queries.py`
- `backend/apps/sessions/tests/test_tasks.py`
- `backend/templates/web/macros/session.html`

### Move

- `apps/runner/providers/` → `libs/providers/`
- `apps/agents/tools/` → `libs/tools/`
- Provider tests → `libs/providers/tests/`

### Modify

- `apps/agents/apps.py`
- `apps/agents/ingest.py`
- `apps/runner/loop.py`
- `apps/runner/backends/base.py`, `django.py`, `memory.py`
- `apps/runner/tests/*` (imports + patches)
- `apps/bus/channels.py`
- `apps/sessions/models.py`, `admin.py`
- `apps/web/views.py`
- `apps/web/tests/test_sse.py`, `test_session_dialog.py`
- `chief/tasks.py`
- `templates/web/session_detail.html`, `dashboard.html`, `agent_detail.html`

### Delete

- `backend/apps/runner/providers/` (after move)
- `backend/apps/agents/tools/` (after move)

---

## Dependency graph (final)

```
libs/providers
libs/tools
libs/algorithms → libs/providers

apps.agents → libs.tools (wiring only)
apps.sessions → agents, bus
apps.sessions.tasks → libs.algorithms, apps.sessions.services
apps.sessions.services.commands → apps.sessions.tasks (lazy), notify, events
apps.runner → libs/providers, libs/tools, apps.agents, apps.sessions, apps.sessions.notify
apps.web → all
```

**No lib imports `apps.*`.** `apps.runner` imports `apps.sessions.services.commands`
only via `backends/django.py` (lazy import acceptable).

---

## Remaining ambiguities (confirm before S5 if desired)

1. **`source` parameter on `update_session_name`** — kept as unused kwarg for
   future manual rename; no column in v1. **OK to ship as stub.**

2. **Naming task when first message is empty string** — `record_input` should not
   be called with empty content (runner already guards); query returns `None` →
   task no-ops. **No change needed.**

3. **Concurrent name writes** — `filter(name__isnull=True).update(...)` is
   sufficient for v1; no row lock. **Acceptable.**

4. **SSE `session_update` deduplication** — v1 sends once per successful rename;
   no revision field. **Acceptable.**

If none of these need discussion, **start at S0**.

---

## Stage exit criteria (definition of done)

- [x] All stages S0–S7 complete.
- [x] `./olib/scripts/orunr py test-all` green (backend; olib mypy pre-existing).
- [x] `./olib/scripts/orunr py lint` and `py mypy` green (backend).
- [ ] **Manual** checklist (top of doc) complete.
