# AGENTS.md - Project Overview

Chief is an agent orchestrator: Django backend, PostgreSQL, Redis/Celery, and
local dev via Docker Compose only (no k8s/deploy targets yet). The dashboard is
server-rendered Jinja + htmx/Alpine — no React frontend.

When doing new things, especially tooling or infra related, look at
`~/yolo/floors` as a reference project.

## Project Structure

```
chief/
├── backend/          # Django project (pkg: `chief`, apps under `apps/`)
├── infra/            # Docker Compose stack + slot overlays
├── config.py         # olib `run` CLI config (compose-only)
└── olib/             # Shared utilities (git submodule)
```

## Common Commands

> **Running project commands**: Use `./olib/scripts/orunr.sh` from the repo root (e.g. `./olib/scripts/orunr.sh py test`). Do **not** call `orunr` directly — it is defined outside this repo and may not be on PATH for agents. Run it **locally** (on the host), not inside a restricted sandbox when possible.
>
> **Do not prefix with `cd`**: The agent shell starts in the repo root, and `orunr.sh` finds the project by walking up from `$PWD` anyway. Run `./olib/scripts/orunr.sh ...` directly — do not wrap commands as `cd /path/to/chief && ./olib/scripts/orunr.sh ...`. The extra `cd` is unnecessary and can trigger additional permission prompts in the agent sandbox.

> **Missing Python environment**: If Python tooling seems missing (e.g. `./olib/scripts/orunr.sh` fails because there is no venv or Python deps), run `./olib/scripts/init.sh` from the repo root to initialize the environment.

> **Cursor sandbox / terminal allowlist**: Agent shell commands should go through `./olib/scripts/orunr.sh` only — **not** `./olib/scripts/orun.sh` and not bare `orunr` on PATH. The allowlist lives in `.cursor/permissions.json` and `.cursor/sandbox.json` (`terminalAllowlist`); patterns must include a `*` suffix (e.g. `./olib/scripts/orunr.sh*`) so subcommands like `py test-all` match. Do **not** add `orun.sh` to the allowlist. Avoid requesting `required_permissions: ["all"]` for routine `orunr.sh` invocations — that bypasses the sandbox and triggers extra approval prompts even when the command is allowlisted.

- **Dependency Init (all configured toolchains)**: `./olib/scripts/orunr.sh dev init`
- **Dependency Sync (all configured toolchains)**: `./olib/scripts/orunr.sh dev sync`
- **Dependency Upgrade (all configured toolchains)**: `./olib/scripts/orunr.sh dev upgrade`
- **Python Dependency Sync**: `./olib/scripts/orunr.sh py sync`
- **Python Dependency Upgrade**: `./olib/scripts/orunr.sh py upgrade`
- **Backend Tests**: `./olib/scripts/orunr.sh py test`
- **Backend Lint**: `./olib/scripts/orunr.sh py lint`
- **Backend Mypy**: `./olib/scripts/orunr.sh py mypy`
- **Development**: `./olib/scripts/orunr.sh docker compose` to run the full stack.
- **Django management**: `./olib/scripts/orunr.sh django manage makemigrations`, `./olib/scripts/orunr.sh django manage migrate`, etc.

### Required checks after changes

- **After any Python changes**: always run `./olib/scripts/orunr.sh py test-all`.

## Postgres restore entrypoints

Compose-only for now (no kubernetes restore target configured yet).

- **Compose restore**: `orun docker postgres-restore <filename> [--slot N] [--cluster dev|pub|kind-test]`
- **List backups**: add `--list`; by default it queries both `onas` and `ponas` backup dirs. You can override with `<filename>` as a directory path or `--backup-dir` (supports local paths and `onas:/...` / `ponas:/...`).
- Compose `.enc` restores default to Knox key lookup at `knox/infrabase/secrets/cnpg/backup-encryption.{cluster}.txt` (`$KNOX` or `~/knox` root).
- Compose cluster inference for Knox key lookup is source-based by default (`ponas:` => `pub`, `onas:` => `dev`) unless overridden with `--cluster`.
- Compose restore defaults (service/db/user/password and `target_backups` for list filtering) are configured in `@docker(...)` in `config.py`; CLI flags override command params.

## Docker Compose slots and DOCO overrides

When running multiple checkouts side-by-side, each uses a **slot** (0, 1, or 2) to avoid port conflicts. The slot is stored in `.doco-slot` (gitignored) or passed as `-s 1` / `-s 2`.

| Slot | Main port (nginx) | Backend (direct) | Postgres | Redis |
|------|-------------------|------------------|----------|-------|
| 0    | 80                | 8000             | 5432     | 6379  |
| 1    | 8081              | 8100             | 5532     | 6479  |
| 2    | 8082              | 8200             | 5632     | 6579  |

- **Slot overlay files**: `infra/docker/overlays/slot-0.env`, `slot-1.env`, `slot-2.env` define `DOCO_*` vars (ports, network, volumes, `DOCO_SITE_DOMAIN`, `DOCO_EMAIL_DIR`, etc.).
- For **slot 1 or 2**, `./olib/scripts/orunr.sh docker compose -s N` runs compose with `--env-file overlays/slot-N.env` for YAML substitution (ports, network, volume names).
- **Baked slot vars**: Before `env.split::compose`, the chosen overlay is written to `.output/compose-slot.env`. The compose env target uses it (or `infra/docker/overlays/slot-0.env` if absent) as **substitutions** when splitting `.env.development.compose`. Placeholders like `{DOCO_SITE_DOMAIN}` in that file are replaced and written into `.output/env.compose.*`. Ports and network/volume names in `docker-compose.yml` still use `${DOCO_*:-default}` and get values from `--env-file overlays/slot-N.env` when slot 1 or 2.
- To add a slot-specific var: add it to each overlay file and use a placeholder in `.env.development.compose` (e.g. `SOME_URL=http://localhost:{DOCO_PORT_FOO}`). No need to duplicate it in `docker-compose.yml` `environment:`.
- **Compose log file (default)**: `.output/compose.log`. `docker compose` tees the same colored terminal stream there (overwritten each run; `down` output is appended). Disable with `compose_log_path=None` on `@docker(...)` in `config.py`.

The dashboard is at the slot's main port; Django admin is at `/admin` (default superuser `admin` / `nimda`).

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
also bakes it into `.output/env.compose.backend` when you run `./olib/scripts/orunr.sh docker compose`.

## Django app dependencies

Backend apps have **one-directional** imports (see `docs/00-design.md`):

| App | Role | May import from |
|-----|------|-----------------|
| `apps.agents` | Domain core: models, `AgentConfigSpec`, tool registry | Django/stdlib only (no other chief apps) |
| `apps.sessions` | Session + event log | `agents` |
| `apps.bus` | Redis pub/sub + mailbox primitives | Django/stdlib only |
| `apps.runner` | Celery step loop, LLM providers, tool invocation | `agents`, `sessions`, `bus` |
| `apps.web` | Dashboard, SSE, control endpoints | all of the above |

Direction: `agents → sessions → runner → web`, with `bus` as a leaf used by `runner` and `web`.

**Rules for agents working on the codebase:**

- Do not import `runner` or `web` from `agents` or `sessions`.
- Provider-specific UI (e.g. listing models for dashboard buttons) belongs in `web`, not `agents`.
- Types referenced by `AgentConfigSpec` stay in `agents` even when `runner` invokes them at runtime.

## Test naming (parproc)

The test runner (parproc) highlights log output when it contains these keywords: **exception**, **error**, **warning**, **notice**, **deprecated**, **deprecation**. Test names appear in logs, so avoid using these words in test names.

Use synonyms that keep the meaning, for example:

- "error" → failure, failure path, invalid input, bad request, etc.
- "exception" → failure, raises (e.g. `test_foo_raises`), throws
- "warning" → caution, advisory, non-fatal
- "deprecated" / "deprecation" → legacy, old path, migration

## For AI Agents

- **Sandbox network**: `.cursor/sandbox.json` allows `curl` (and other HTTP clients) to reach the local dev server on `localhost` / `127.0.0.0/8` (slots 0–2: nginx ports 80, 8081, 8082; backend direct ports 8000, 8100, 8200).
- **Web login for debugging**: The dashboard and session UI require a Django session. Log in at `http://localhost/admin/` (or the slot's nginx port) with `admin` / `nimda`, then return to `/` or a session URL. Agents debugging UI or API issues should do this first — unauthenticated requests won't see bootstrap/start controls or an owned agent list.
- **Compose logs**: Default path `.output/compose.log` (see Docker Compose slots above). Read it when debugging stack issues without a live terminal attach.
- Follow established patterns in the backend codebase
- Always run tests before committing changes
- Never include "Made with cursor" in commit messages
- When writing code, add comments that explain what major components are responsible for, and document non-obvious/tricky logic (or background assumptions) when that context is not apparent from the code itself. Keep them concise and to the point — comment the "why", not the obvious "what".
- Prefer direct imports from canonical module paths over compatibility re-export bridge files. When code moves, update call sites and remove obsolete pass-through files.
- For Django schema changes, never manually write migration files; always generate migrations using Django migration tooling/commands.
- **Do not add license headers or copyright statements to files** — tooling/pre-commit will add them later
