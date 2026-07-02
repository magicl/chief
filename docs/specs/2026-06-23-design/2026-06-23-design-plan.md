# Chief — v0.1 Implementation Plan

Companion to `2026-06-23-design-design.md`. This breaks v0.1 into milestones with concrete,
ordered tasks, exact file locations, and explicit callouts for the parts that
are likely to be tricky. Read `2026-06-23-design-design.md` first for the *why*; this doc is
the *how*.

v0.1 goal (from the design): **create an agent from a hardcoded spec, manually
start a session, watch it stream, chat with it, pause/resume it — with the full
event log persisted and resumable.** First provider: **OpenAI**.

---

## Current status (2026-06-25)

**Overall:** M0–M5 are implemented in code; the vertical slice is demoable via
Docker Compose with `OPENAI_API_KEY` set in `.env.local`. M6 is partially done —
automated tests cover most resumability logic, but `py test-all` is not fully green
yet and a few manual hardening checks remain.

| Milestone | Status | Notes |
|-----------|--------|-------|
| M0 Foundations | **Done** | Apps, ASGI, nginx SSE location, SSE spike at `/debug/sse-spike/`, `openai` dep, task import, `SubmoduleRoots('olib', …)` in `config.py` |
| M1 Data model | **Done** | Migrations applied; `apps.sessions` uses app label `agent_sessions` (avoids clash with Django's `sessions` app) |
| M2 Event log | **Done** | `events.py`, `rebuild.py`, unit tests |
| M3 Runner | **Done** | Provider + loop + `run_session` + `run_agent` command; headless path needs real `OPENAI_API_KEY` to exercise |
| M4 Bus | **Done** | Redis list mailbox, pub/sub, session lock, `runner/dispatch.py`; bus unit tests mock Redis |
| M5 UI | **Done** | Dashboard, session detail, SSE replay+tail, chat/pause/resume/abort; EventSource + Alpine (not htmx SSE extension) |
| M6 Hardening | **In progress** | Resume/RESTART unit tests; worker pool in `entrypoint.sh` + `AGENTS.md`; see gaps below |

**Remaining before calling v0.1 done:**

- Fix `apps/web/tests/test_sse.py` (AsyncClient + async streaming response).
- Fix mypy on `apps/agents/hardcoded.py` (`AbstractBaseUser` vs `User` lookup).
- Manual: kill worker mid-run → redispatch → confirm `RESTART` + no duplicate work.
- Manual: flush Redis → reload session page → confirm DB replay + live tail.
- Manual: confirm SSE spike streams incrementally through nginx (`curl -N`).
- Optional: gate a live OpenAI integration test behind `--live` / `TEST_LIVE`.

**Deviations / decisions locked in:**

- Mailbox: Redis **list** (FIFO drain), not stream.
- `apps/runner/dispatch.py`: `maybe_dispatch_session`, `push_chat_and_dispatch`.
- Session lock: `SET NX EX` in `apps/bus/channels.py` (no heartbeat yet).
- Tool openai names: `{tool}.{function}` (e.g. `clock.now`).

---

## Conventions & layout

New Django apps live under `backend/apps/<name>/` with `AppConfig.name =
'apps.<name>'` (matches the existing `apps.web`). Register each in
`chief/settings.py` `INSTALLED_APPS`.

```
backend/apps/
  agents/        # domain core: Agent, AgentConfig, Trigger, spec, tools/
    spec.py            # AgentConfigSpec pydantic models
    models.py
    tools/
      base.py          # Tool base + registry
      builtin.py       # one trivial tool for v0.1
    hardcoded.py       # the single v0.1 spec + bootstrap helper
    management/commands/
  sessions/      # AgentSession, AgentSessionEvent, event log + rebuild
    models.py
    events.py          # append/query (single-writer)
    rebuild.py         # events -> provider-neutral message list
  bus/           # thin Redis primitives (no domain logic)
    client.py          # raw sync + async redis clients
    channels.py        # pub/sub publish + mailbox push/drain
  runner/        # the executor
    providers/
      base.py          # provider interface
      openai.py        # OpenAI impl
      pricing.py       # model -> $/token table
    loop.py            # checkpointed step loop
    tasks.py           # @shared_task run_session
    management/commands/
```

Dependency direction (enforce by imports only): `agents → sessions → runner →
web`; `bus` is a leaf used by `runner` and `web`.

The sessions app is registered as `apps.sessions` but uses Django app label
`agent_sessions` to avoid clashing with `django.contrib.sessions`.

Root `config.py` includes `SubmoduleRoots('olib', aliases=['./backend/olib'])`
(same pattern as floors). Python imports use `backend/olib -> ../olib`.

Commands (host, from repo root):
- Tests: `./olib/scripts/orunr.sh py test-all` (required after any Python change)
- Lint / mypy: `./olib/scripts/orunr.sh py lint` / `... py mypy`
- Migrations: `./olib/scripts/orunr.sh django manage makemigrations` then `... migrate`
  (never hand-write migrations)
- Stack: `./olib/scripts/orunr.sh docker compose`

---

## Known hard problems & key decisions

These cut across milestones. Decide them once; they shape everything.

1. **Single active runner per session.** A session must never have two runner
   tasks executing concurrently (double LLM calls, seq races, duplicate
   events). Use a Redis lock keyed by session id (`SET nx ex` with a heartbeat,
   or a Redlock-style token) acquired at the top of `run_session` and released
   on exit. Resume/dispatch must be a no-op if the lock is held.

2. **Single-writer event log.** To avoid `seq` contention, **only the runner
   writes `AgentSessionEvent` rows.** The web layer never writes events
   directly — chat/pause/resume only push to the Redis mailbox. The UI renders
   the user's message optimistically and reconciles when the real `INPUT` event
   streams back. This keeps `seq` allocation inside the one process that holds
   the session lock.

3. **`seq` allocation.** Per-session monotonic integer. With single-writer this
   is `max(seq)+1` for the session, allocated inside the same transaction as the
   insert, with a `unique_together(session, seq)` constraint as a backstop.

4. **"Waiting for input" releases the worker.** When the agent needs user input,
   the task sets `status=waiting`, releases the lock, and **returns** (does not
   block a prefork slot). Resume = a *new* `run_session` task dispatched when a
   mailbox message arrives. Re-queueing only happens on these input boundaries —
   never per step (per the design, to preserve prompt caches).

5. **Gap-free attach (SSE).** Pub/sub is live-only; a late subscriber misses
   events, and a subscribe-then-replay sequence can drop or duplicate events.
   Solution: the **DB event log (ordered by `seq`) is the source of truth for
   replay**; pub/sub is only a "new events exist" signal. The SSE view: (a)
   replays all persisted events for the session, tracking `last_seq`; (b) then
   forwards live events, discarding any with `seq <= last_seq`. The client also
   dedupes by `seq`. (v0.1 fallback if pub/sub proves fiddly: SSE polls the DB
   for `seq > last_seq` every ~500ms — gap-free and trivial, at the cost of a
   little latency.)

6. **Celery queue constraints (olib).** `olib/.../initialization.py` sets
   `task_create_missing_queues = False` and defines only a `default` queue. For
   v0.1, **run agent sessions on `default`**; do not invent a new queue yet.
   Because agent tasks are long-lived and I/O-bound, run the worker with a
   thread/gevent pool and higher concurrency (e.g. `--pool=threads
   --concurrency=16`) rather than the default prefork-per-CPU, so a handful of
   live sessions don't starve the worker. (A dedicated `agent-runs` queue +
   worker is a later optimization and would require overriding
   `app.conf.task_queues` in `chief/celery.py` after `initCelery`.)

7. **Task discovery.** olib only auto-imports `chief.tasks`. So
   `backend/chief/tasks.py` **must import `apps.runner.tasks`** for the
   `@shared_task` to register on the worker (the file already documents this).

8. **Eager mode in tests/local-without-redis.** `settingsbase` sets
   `CELERY_WORKERS_ALWAYS_EAGER = True` when `REDIS_URL` is unset. Eager runs the
   long loop inline in the caller — fine for tests with a mocked provider, but
   the loop must be safe to run synchronously (no reliance on a separate worker
   existing). Guard tests so they never hit the real OpenAI API or real Redis.

9. **Async SSE view + sync ORM.** Django async views cannot call the sync ORM
   directly. Use `asgiref.sync.sync_to_async` for replay queries and
   `redis.asyncio` for the live subscription. The runner (sync Celery) uses the
   sync redis client.

10. **nginx buffers SSE by default.** The dev proxy (`infra/docker/nginx.conf`)
    will buffer `text/event-stream`, breaking streaming. Add `proxy_buffering
    off;`, `proxy_cache off;`, HTTP/1.1 (`proxy_http_version 1.1;
    proxy_set_header Connection "";`), and a long `proxy_read_timeout` for the
    backend location (ideally a dedicated `location` for the SSE path). Also send
    `X-Accel-Buffering: no` from the view as a belt-and-suspenders.

11. **Circular FK `Agent` ↔ `AgentConfig`.** `Agent.current_config` →
    `AgentConfig`, and `AgentConfig.agent` → `Agent`. Make `current_config`
    nullable with `on_delete=SET_NULL`, `related_name='+'`; create the `Agent`,
    then the `AgentConfig`, then set `current_config`.

12. **OpenAI cost + streaming usage.** Token usage is only returned on streamed
    chat completions if you pass `stream_options={"include_usage": True}` (usage
    arrives in the final chunk). `cost_usd` requires a per-model price table
    (`runner/providers/pricing.py`) that must be kept current; treat unknown
    models as `cost_usd=None`, not 0.

---

## Milestones

Each milestone is independently demoable. Check tasks off as completed.

### M0 — Foundations & plumbing

- [x] Create the four app skeletons (`agents`, `sessions`, `bus`, `runner`) with
      `apps.py` (`name = 'apps.<n>'`), empty `models.py`, `__init__.py`.
- [x] Add all four to `INSTALLED_APPS` in `chief/settings.py`.
- [x] Add `openai` to `backend/pyproject.toml`; `./olib/scripts/orunr.sh py sync`.
- [x] Set `ASGI_APPLICATION = 'chief.asgi.application'` in settings (uvicorn
      already serves `chief.asgi`); leave `WSGI_APPLICATION` or drop it.
- [x] Import `apps.runner.tasks` from `backend/chief/tasks.py` (see decision #7).
- [x] **SSE spike**: a throwaway async view that streams 5 timestamped events;
      wire nginx (decision #10) and confirm `curl -N http://localhost/<spike>`
      shows incremental output *through nginx*, not buffered.
- [x] Confirm `OPENAI_API_KEY` is plumbed via env (`.env.development.compose` →
      `.output/env.compose.backend`); no real calls yet.
- [x] `SubmoduleRoots('olib', aliases=['./backend/olib'])` in root `config.py`
      (matches floors; required for correct test/mypy root ownership).

Acceptance: stack boots; SSE spike streams through nginx; worker starts and sees
no missing-task errors.

⚠ Tricky: nginx buffering (#10); olib task import (#7); eager-vs-worker (#6, #8).

**Status:** Code complete. Manual nginx spike check still worth doing once per environment.

### M1 — Data model (agents + sessions)

- [x] `apps/agents/spec.py`: the `AgentConfigSpec`, `LLMSpec`, `TriggerSpec`,
      `ToolPermission` pydantic models from the design.
- [x] `apps/agents/models.py`: `Agent`, `AgentConfig`, `Trigger`. UUID PKs
      (`UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`).
      `Agent.user` → `settings.AUTH_USER_MODEL`. Circular FK per decision #11.
      Store `AgentConfig.spec` as JSON = `AgentConfigSpec.model_dump(mode="json")`,
      re-validated on read via a typed accessor.
- [x] `apps/sessions/models.py`: `AgentSession`, `AgentSessionEvent`. Include
      cost fields (`model`, `input_tokens`, `output_tokens`, `cost_usd`,
      `latency_ms`), `kind` choices, `seq` with `unique_together=(session, seq)`,
      and indexes on `(session, seq)` and `(session, created_at)`.
- [x] `apps/agents/tools/base.py`: `Tool` base + a registry; `builtin.py`: one
      trivial tool (e.g. `clock.now`) to exercise the tool path.
- [x] `apps/agents/hardcoded.py`: the single v0.1 `AgentConfigSpec` + a
      `bootstrap_agent(user)` helper that creates `Agent` + `AgentConfig`, derives
      immutable `Trigger` rows from `spec.triggers`, and sets `current_config`.
- [x] `management/commands/bootstrap_agent.py` calling the helper.
- [x] Register all models in admin (read-only inlines for events help debugging).
- [x] `makemigrations` + `migrate`.

Acceptance: `manage.py bootstrap_agent` creates an agent + config + manual
trigger; visible in `/admin/`.

⚠ Tricky: circular FK (#11); JSON round-trip validation; enum choices match the
design's status/kind values.

**Status:** Done. App label `agent_sessions` for the sessions app.

### M2 — Event log + state rebuild

- [x] `apps/sessions/events.py`: `append_event(session, kind, payload, **cost)`
      allocating `seq` in-transaction (decision #2/#3); `events_for(session)`.
- [x] `apps/sessions/rebuild.py`: `rebuild_messages(session)` → provider-neutral
      message list (system + alternating user/assistant + tool_call/tool_result
      pairs) reconstructed purely from events, deterministic and ordered by
      `seq`. Define the canonical `payload` shape per `kind` here.
- [x] Unit tests: append ordering/seq uniqueness; rebuild pairs `TOOL_CALL` with
      its `TOOL_RESULT`; `FAILURE`/`RESTART` events don't corrupt the message
      list.

Acceptance: tests prove a sequence of events rebuilds to the exact expected
message list.

⚠ Tricky: tool_call/tool_result pairing and id matching; defining payload shapes
now so the provider layer can rely on them.

**Status:** Done (`apps/sessions/tests/test_events.py`, `test_rebuild.py`).

### M3 — Provider abstraction + runner loop (headless)

- [x] `runner/providers/base.py`: interface — `stream(messages, tools) ->
      Iterator[Delta]` yielding text/tool-call deltas and returning final
      `Usage` (tokens) + assembled tool calls.
- [x] `runner/providers/openai.py`: implement with the `openai` SDK; pass
      `stream_options={"include_usage": True}`; translate `agents` tool defs →
      OpenAI tool schema; assemble streamed tool-call fragments.
- [x] `runner/providers/pricing.py`: model → price table; compute `cost_usd`
      (unknown model ⇒ `None`).
- [x] `runner/loop.py`: the checkpointed step loop — load pinned `AgentConfig`,
      `rebuild_messages`, call provider, append `OUTPUT` / `TOOL_CALL` /
      `TOOL_RESULT` events with cost, enforce `ToolPermission` (deny wins) before
      invoking a tool, emit `FAILURE` on exceptions and `RESTART` on resume.
- [x] `runner/tasks.py`: `@shared_task run_session(session_id)` — acquire the
      per-session lock (#1), set `status` transitions
      (`queued→running→…→done/failed/waiting`), call the loop, release lock.
- [x] `runner/management/commands/run_agent.py`: create an `AgentSession` from
      the agent's manual trigger and dispatch `run_session`.

Acceptance: `manage.py run_agent <identifier>` runs a real session end-to-end
headless; events + cost persisted; the trivial tool is invoked and its result
recorded.

⚠ Tricky: session lock (#1); streamed usage + pricing (#12); tool-schema
generation from pydantic; assembling streamed tool-call deltas; key handling.

**Status:** Done in code. End-to-end with real OpenAI requires `OPENAI_API_KEY` in
`.env.local` (or compose env). Loop tests mock the provider
(`apps/runner/tests/test_loop.py`).

### M4 — Realtime bus

- [x] `apps/bus/client.py`: `sync_client()` and `async_client()` building raw
      redis clients from `REDIS_URL`, namespaced with `CACHE_PREFIX`
      (`settingsbase` requires `REDIS_PREFIX`). These are **not** the Django
      cache.
- [x] `apps/bus/channels.py`: `publish_event(session_id, event_dict)` (pub/sub);
      `mailbox_push(session_id, msg)` / `mailbox_drain(session_id)` (Redis list
      or stream).
- [x] Runner integration: after each `append_event`, `publish_event`; at every
      checkpoint, `mailbox_drain` and act (inject chat as `INPUT`, pause, resume,
      abort); on waiting-for-input set `status=waiting` and return (#4).
- [x] Resume path: a function the web layer calls to dispatch `run_session` iff
      the session is `waiting`/`paused` and the lock is free (guard double
      dispatch via the lock / a `SETNX` dispatch flag).
- [x] Tests with a fake redis (or real redis in compose): mailbox push →
      drain; pause then resume continues from the right `seq`.

Acceptance: pushing a mailbox message to a waiting session triggers a resume that
continues the conversation; pause stops cleanly after the current step.

⚠ Tricky: gap-free attach (#5); resume dispatch while no runner is live; avoiding
double dispatch; only `session_id` crosses the task boundary (pickle-safe).

**Status:** Done. Resume helpers in `apps/runner/dispatch.py`. Bus tests mock
Redis (`apps/bus/tests/test_channels.py`). Pause/resume seq continuity covered
indirectly via runner task tests.

### M5 — UI (dashboard + live session)

- [x] `apps/web` views: dashboard listing agents + recent sessions; session
      detail page.
- [x] SSE endpoint (async view): replay events from DB via `sync_to_async`
      tracking `last_seq`, then subscribe via `redis.asyncio` and forward
      `seq > last_seq` (#5, #9). Set `Content-Type: text/event-stream` and
      `X-Accel-Buffering: no`; handle client disconnect to close the subscription.
- [x] Control endpoints (POST): `chat` (mailbox push + optimistic echo),
      `pause`, `resume`, `abort`. CSRF tokens included via htmx
      (`hx-headers` / cookie). Resume/abort call the M4 resume path.
- [x] Templates (Jinja + htmx `sse` extension + Alpine): event stream rendered
      incrementally, client dedupes by `seq`; chat box; pause/resume/abort
      buttons reflecting `status`.

Acceptance: open a session, watch tokens stream live, send a chat message and see
the agent respond, pause and resume from the UI.

⚠ Tricky: async view + sync ORM (#9); htmx sse event naming + swap target;
reconnect / `Last-Event-ID`; nginx buffering again (#10); CSRF with htmx.

**Status:** Done. Live events use browser `EventSource` + Alpine (not htmx SSE
extension). CSRF for htmx POSTs via `htmx:configRequest` in `web/base.html`.
Routes: `/`, `/sessions/<id>/`, `/sessions/<id>/events/`, control POSTs under
`/sessions/<id>/{chat,pause,resume,abort}/`.

### M6 — Hardening & resumability

- [ ] Kill the worker mid-run; redispatch; confirm the loop rebuilds from events,
      emits `RESTART`, and continues without duplicating work.
- [ ] Flush Redis, reload the session detail page; confirm replay still works
      from the DB (cold-attach) and live streaming re-establishes.
- [x] Worker pool/concurrency: document the thread/gevent pool command (#6) and
      pick sane concurrency; verify several concurrent sessions don't starve.
- [ ] Full test pass: `./olib/scripts/orunr.sh py test-all`; gate any live
      OpenAI test behind `--live`.

Acceptance: resume-after-kill and cold-attach both work; tests green.

**Status:** In progress.

- [x] `entrypoint.sh`: `--pool=threads --concurrency=16` for celery-worker.
- [x] Documented in `AGENTS.md` (worker pool + v0.1 quick start).
- [x] Unit tests: `RESTART` on resume, no `RESTART` on first run
      (`apps/runner/tests/test_tasks.py`); SSE DB replay test started
      (`apps/web/tests/test_sse.py` — currently failing on async stream read).
- [ ] Fix remaining test/mypy failures blocking `py test-all`.
- [ ] Manual kill-worker and flush-Redis checks (see **Remaining** above).

**Known test gaps (2026-06-25):** `test_sse.py` async streaming assertion;
`hardcoded.py` mypy `User` lookup type.

---

## Open implementation questions

- **Mailbox storage**: Redis list (simple FIFO drain) vs Redis stream
  (IDs + consumer groups). **Decided:** list (`apps/bus/channels.py`).
- **Tool-name validation at ingest**: should spec validation fail if a
  `ToolPermission.tool` doesn't exist in the `agents/tools` registry? Cheap to
  add once the registry is real; currently deferred.
- **Interrupt-now vs queued injection** (from `2026-06-23-design-design.md` open questions):
  v0.1 ships queued injection at the next checkpoint only.
- **Dedicated `agent-runs` queue**: deferred to a later version (requires
  overriding `app.conf.task_queues`); v0.1 uses `default` with a tuned pool.
- **Live OpenAI test**: not yet gated behind `--live` / `TEST_LIVE`.
