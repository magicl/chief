# Chief

Agent orchestrator.

Chief runs many **agents** that perform routine tasks, plus **monitor agents**
that watch the routine agents. Work flows through **queues**, which can be fed by
external sources (email, webhooks, ...) or by internal events (e.g. completed
runs from another agent). So an agent can act on the outside world, and also act
on the results produced by other agents.

## Stack

- **Django 5** (server-rendered Jinja templates, htmx/Alpine where useful — no React)
- **PostgreSQL** for persistence
- **Redis** as the Celery broker / result backend / cache
- **Celery** worker + beat for running agents and periodic monitors
- Local dev runs entirely via **Docker Compose** (no k8s/deploy targets yet)

## Layout

```
chief/
├── backend/          # Django project (project pkg: `chief`, apps under `apps/`)
├── infra/            # Docker Compose stack + slot overlays
├── config.py         # olib `run` CLI config (compose-only)
└── olib/             # Shared utilities (git submodule)
```

### Apps

- `apps/web` — the Jinja dashboard (currently a placeholder).

The core **agents** and **queues** domain is still being designed and has not been
implemented yet. Planned shape:

- agents — `Agent` (routine or monitor) and `Run` records, plus the Celery tasks
  that execute agents and monitors.
- queues — `Queue` (internal or external source) and `QueueItem` work units that
  agents consume and produce.

## Planned / not yet implemented

### Realtime updates via Django signals → websockets

The dashboard currently refreshes via htmx polling. We want to move to pushing
live updates to the frontend over **websockets**, driven by **Django signals**:

- Domain changes (e.g. `Run` status transitions, new `QueueItem`s) emit Django
  signals — either the built-in `post_save`/`post_delete` or custom signals we
  define for meaningful orchestration events.
- A thin signal layer translates those into messages fanned out to subscribed
  websocket clients (channel/group per agent, queue, or run), so the dashboard
  updates without polling.
- This keeps the source of truth in the ORM while signals act as the single
  integration point for realtime push.

Open decisions (transport, e.g. Django Channels vs. an external pub/sub bridge)
are deferred; this is a placeholder for the intended design only.

## Documentation

- [Agent format, tools, triggers, queues, and credentials](docs/docs/agents.md)

## Development setup

Bootstrap, dependency sync, lint/test commands, and Docker Compose (DOCO slots) are documented in:

- [`olib/README.md`](olib/README.md) — adding olib, init, and CLI overview
- [`AGENTS.md`](AGENTS.md) — shared agent instructions (symlinked from olib); includes the **docker-compose** skill
- [`AGENTS.local.md`](AGENTS.local.md) — Chief-specific dev notes (Celery, app layout, local URLs)

First-time setup from repo root:

```bash
./olib/scripts/init.sh
./olib/scripts/orunr docker compose
```

The dashboard is at the slot's nginx port (`DOCO_PORT` in the active overlay). Django admin is at `/loelabs-admin/` (default superuser `admin` / `nimda`).

## Git commit messages

```
feat: add email queue connector
fix: handle empty agent run output
chore: bump deps
wip: in-progress stuff
```
