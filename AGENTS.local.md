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

See `docs/specs/2026-07-01-chat-names/2026-07-01-chat-names-design.md` for the libs/services/notifications design.

See `docs/ARCHITECTURE.md` (credentials section) and
`docs/specs/2026-07-03-key-management/` for **credentials & secrets**.

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
celery -A chief worker --loglevel=WARNING --pool=threads --concurrency=16
```

(`backend/entrypoint.sh` passes these flags for the `celery-worker` container.)

v0.1 runs sessions on the default Celery queue; a dedicated `agent-runs` queue is
deferred.

## Agent v0.1 quick start

Open the dashboard at `/` (log in via the header link; default superuser `admin` / `nimda`).

1. Click a **model button** (OpenAI / Anthropic / Local) to create a demo agent
2. Click **Start** on the new agent row
3. Chat on the session page; event log streams via SSE

LLM API keys: **Settings → Keys** (encrypted store) or `.env.local` under `#[backend]`
(env is fallback only when no encrypted credential exists). See `docs/ARCHITECTURE.md`.

Docker Compose loads `.env.local` into backend/worker containers (optional file) and bakes
it into `.output/env.compose.backend` when you run `./olib/scripts/orunr docker compose`.

## Architecture

Backend architecture (three-layer request handling, app dependencies, lib rules,
services pattern, Celery tasks, SSE notifications) is documented in
**`docs/ARCHITECTURE.md`** — the canonical reference for all structural decisions.

See `docs/ARCHITECTURE.md` for the libs table, dependency graph, and import rules.

## Credentials & secrets

Rules and wiring patterns: **`docs/ARCHITECTURE.md`** (credentials section).
Implementation spec: `docs/specs/2026-07-03-key-management/`.

## Agent config schema migrations

Design: `docs/specs/2026-07-03-agent-config-schema/`.

**Every breaking change to `AgentConfigSpec` must include a spec migration** — not a
Django data migration on `AgentConfig.spec` JSON.

**When to bump `schema_version`:** only when older stored specs cannot be read correctly
without transformation (renamed/removed fields, type changes, semantic changes). **Do not
bump** for backward-compatible additions (new optional fields with defaults, new optional
list entries) — pydantic and YAML loaders accept those on the current version as-is.

### How it works

- **`apps/agents/spec_migrations/migrations/`** — one file per upgrade step, named
  **`NNN_{short_descriptive_name}.py`** (e.g. `001_tool_instances.py`). Version **0** is
  the initial shape; **`001_…` upgrades 0 → 1**, `002_…` upgrades 1 → 2, etc. Each module
  exports `FROM_VERSION`, `TO_VERSION`, and `upgrade(raw) -> dict`.
- **`spec_migrations/registry.py`** discovers files under `migrations/`, sorts by `NNN`,
  and verifies a contiguous chain. Discovery is **`@functools.cache`d** — one scan per
  process. Bump **`AGENT_CONFIG_SPEC_VERSION`** in `spec.py` to match the latest
  `TO_VERSION` when you add a breaking migration.
- **Load** — always call **`load_spec_dict()`** (via `AgentConfig.get_spec()` or
  `spec_loader`). Applies the upgrade chain; returns the **current** pydantic shape in
  memory. Old stored rows keep working without being rewritten.
- **Save** — always write **`spec_version`** + JSON at the latest version as a **new**
  `AgentConfig` row (never update an existing row’s spec in place).
- **Django migrations** — may add/alter columns (e.g. `AgentConfig.spec_version`); **never**
  RunPython that transforms spec JSON.

No bulk upgrade management command — persisting an upgraded spec is the user’s explicit
save (avoids clobbering in-progress edits).

### Checklist when changing the schema (breaking bump only)

Skip this checklist for optional-only additions — update `AgentConfigSpec` and tests only.

1. Update **`AgentConfigSpec`** (current version only) in `apps/agents/spec.py`.
2. Add **`apps/agents/spec_migrations/migrations/NNN_{short_name}.py`** where `NNN` is the
   new target version zero-padded to three digits (e.g. `002_add_queue_bindings.py` for 1→2).
   Export `FROM_VERSION`, `TO_VERSION`, and `upgrade()`.
3. Registry auto-discovers the new file; confirm the chain is contiguous at startup/tests.
4. Bump **`AGENT_CONFIG_SPEC_VERSION`** to the new `TO_VERSION`.
5. **Tests** in `apps/agents/tests/test_spec_migrations.py`:
   - unit test the new step with fixture dicts (before → after);
   - chain test from version 0 (and each intermediate) to current;
   - `get_spec()` on a model row at the previous `spec_version`.
6. Update **`HARDCODED_SPEC`**, YAML fixtures, and docs/examples to the new version.
7. Django migration **only** if new columns/indexes are needed — not for JSON rewrites.

**Additional codebase rules:**

- **`AgentConfigSpec` schema changes:** follow **Agent config schema migrations** above —
  add a spec migration step and tests; do not transform spec JSON in Django data migrations.
- Algorithm config: pydantic struct per algorithm with defaults; override on call — avoid new env vars for tuning.

## For AI agents (Chief-specific)

- **Sandbox network**: `.cursor/sandbox.json` allows `curl` (and other HTTP clients) to reach the local dev server on `localhost` / `127.0.0.0/8`. Port numbers per slot are in `infra/docker/overlays/slot-*.env` (see olib **docker-compose** skill).
- **Terminal allowlist**: `.cursor/permissions.json` and `.cursor/sandbox.json` allow `./olib/scripts/orunr` only (patterns need a `*` suffix for subcommands). Do not add `orun` to the allowlist.
- **Web login for debugging**: The dashboard and session UI require a Django session. Log in at `http://localhost/admin/` (or the slot's nginx port) with `admin` / `nimda`, then return to `/` or a session URL. Agents debugging UI or API issues should do this first — unauthenticated requests won't see bootstrap/start controls or an owned agent list.
- Follow established patterns in the backend codebase.
