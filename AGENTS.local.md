This file holds project-specific agent instructions. Shared instructions live in `AGENTS.md` (symlinked from olib).

# Chief — project notes

Chief is an agent orchestrator: Django backend, PostgreSQL, Redis/Celery, and
local dev via Docker Compose only (no k8s/deploy targets yet). The dashboard is
server-rendered Jinja + htmx/Alpine — no React frontend.

When doing new things, especially tooling or infra related, look at
`~/yolo/floors` as a reference project.

## Project structure

```
chief/
├── backend/          # Django project (pkg: `chief`, apps under `apps/`)
│   ├── apps/         # Django apps (domain + HTTP/Celery transport)
│   ├── libs/         # Shared libraries (providers, tools, algorithms)
│   └── chief/        # Project shell (settings, celery, task registry)
├── infra/            # Docker Compose stack + slot overlays
├── config.py         # olib `run` CLI config (compose-only)
└── olib/             # Shared utilities (git submodule)
```

See `docs/02-chat-names.md` for the libs/services/notifications design.

## Chief-specific commands

- **Development**: `./olib/scripts/orunr docker compose` to run the full stack.
- **Django management**: `./olib/scripts/orunr django manage makemigrations`, `./olib/scripts/orunr django manage migrate`, etc.

## Postgres restore

Compose-only for now (no kubernetes restore target configured yet).

## Local URLs

The dashboard is at the slot's nginx port (`DOCO_PORT` in `infra/docker/overlays/slot-*.env`); Django admin is at `/admin` (default superuser `admin` / `nimda`). DOCO slots and ports: olib **docker-compose** skill (`olib/ai/skills/docker-compose/SKILL.md`).

## Celery worker (agent sessions)

Agent session tasks are long-lived and I/O-bound. The dev worker uses a thread pool
so several concurrent sessions do not each occupy a prefork slot:

```bash
celery -A chief worker --loglevel=INFO --pool=threads --concurrency=16
```

(`backend/entrypoint.sh` passes these flags for the `celery-worker` container.)

v0.1 runs sessions on the default Celery queue; a dedicated `agent-runs` queue is
deferred.

## Agent v0.1 quick start

Open the dashboard at `/` (log in via the header link; default superuser `admin` / `nimda`).

1. Click a **model button** (OpenAI / Anthropic / Local) to create a demo agent
2. Click **Start** on the new agent row
3. Chat on the session page; event log streams via SSE

Set LLM API keys in `.env.local` under a `#[backend]` group (see `.env.local.example`).
Docker Compose loads `.env.local` directly into backend/worker containers (optional file) and
also bakes it into `.output/env.compose.backend` when you run `./olib/scripts/orunr docker compose`.

## Backend libs (`backend/libs/`)

Shared, Django-free packages live under `backend/libs/` (plural container):

| Package | Role |
|---------|------|
| `libs/providers` | LLM provider implementations |
| `libs/tools` | Tool definitions + registry |
| `libs/algorithms` | Reusable algorithms (may call providers) |

**Lib rules:**

- Libs do **not** import `apps.*`.
- Minimize coupling between libs; use one-directional deps and public interfaces.
- Apps orchestrate; libs compute.
- When a lib needs app/domain access later, **inject at the app boundary** (see
  `apps.agents` tool wiring) — do not import Django from libs.

```
libs/providers          (stdlib + vendor SDKs)
libs/tools              (stdlib + pydantic)
libs/algorithms    -->  libs/providers
apps/*             -->  libs/* (as needed)
```

## Django app dependencies

Backend apps have **one-directional** imports (see `docs/00-design.md`):

| App | Role | May import from |
|-----|------|-----------------|
| `apps.agents` | Domain core: models, `AgentConfigSpec`, tool wiring | Django/stdlib, `libs.tools` |
| `apps.sessions` | Session + event log + session services/tasks | `agents`, `bus`, `libs.algorithms` (tasks only) |
| `apps.bus` | Redis pub/sub + mailbox primitives | Django/stdlib only |
| `apps.runner` | Celery step loop, tool invocation | `agents`, `sessions`, `bus`, `libs.providers`, `libs.tools` |
| `apps.web` | Dashboard, SSE, control endpoints | all of the above |

Direction: `agents → sessions → runner → web`, with `bus` as a leaf used by `runner` and `web`.

## App services (queries + commands)

Each app exposes a **public API** for other apps and Celery tasks via
`apps/<app>/services/`:

| Module | Purpose |
|--------|---------|
| `services/queries.py` | Read-only domain access (no bus publish, no task scheduling) |
| `services/commands.py` | Mutations: DB writes, notifications, downstream `.delay()` |

**Rules:**

- Celery tasks, runner, and web views call **services**, not raw ORM updates
  (when a service exists).
- Tasks are **thin orchestrators**: query → lib function → command.
- Commands that mutate session/agent state emit UI notifications (see below).

Example (sessions): `get_first_input_text` (query), `record_input` /
`update_session_name` (commands).

## Celery tasks

- Each app that needs async work owns **`apps/<app>/tasks.py`**.
- Register task modules in **`chief/tasks.py`** (imports only — see existing
  `apps.runner.tasks` pattern).
- **`apps.runner.tasks`**: long-lived session execution (`run_session`).
- **`apps.sessions.tasks`**: short metadata side work (e.g. `generate_session_name`).
- Tasks never call `publish_*` directly; commands own side effects.

## Real-time UI notifications (SSE)

Session-scoped Redis pub/sub carries an envelope:

- `session_event` — `AgentSessionEvent` payload (dedupe by `seq` in SSE)
- `session_update` — partial session patch, e.g. `{"name": "..."}`

Commands call `publish_session_update` after DB writes. The session detail page
listens on the existing SSE connection and patches Alpine state (no HTMX swap
required for simple fields).

**Rules for agents working on the codebase:**

- Do not import `runner` or `web` from `agents` or `sessions`.
- Provider-specific UI (e.g. listing models for dashboard buttons) belongs in `web`, not `agents`.
- Types referenced by `AgentConfigSpec` stay in `agents` even when `runner` invokes them at runtime.
- Algorithm config: pydantic struct per algorithm with defaults; override on call — avoid new env vars for tuning.

## For AI agents (Chief-specific)

- **Sandbox network**: `.cursor/sandbox.json` allows `curl` (and other HTTP clients) to reach the local dev server on `localhost` / `127.0.0.0/8`. Port numbers per slot are in `infra/docker/overlays/slot-*.env` (see olib **docker-compose** skill).
- **Terminal allowlist**: `.cursor/permissions.json` and `.cursor/sandbox.json` allow `./olib/scripts/orunr` only (patterns need a `*` suffix for subcommands). Do not add `orun` to the allowlist.
- **Web login for debugging**: The dashboard and session UI require a Django session. Log in at `http://localhost/admin/` (or the slot's nginx port) with `admin` / `nimda`, then return to `/` or a session URL. Agents debugging UI or API issues should do this first — unauthenticated requests won't see bootstrap/start controls or an owned agent list.
- Follow established patterns in the backend codebase.
