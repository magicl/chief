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

## Development setup

Python deps are managed via `uv` (root `pyproject.toml` + per-root lockfiles).

```bash
# First-time bootstrap
orunr dev init

# Sync deps after pulling
orunr dev sync
```

Run the full stack with Docker Compose:

```bash
orunr docker compose            # reads slot from .doco-slot (default 0)
orunr docker compose -s 1       # explicit slot override
```

The dashboard is then available at the slot's main port (see below). The Django
admin lives at `/admin` (default superuser `admin` / `nimda`).

### Checks

```bash
orunr py test       # lint + mypy + django tests for the backend root
orunr py lint
orunr py mypy
```

You can run `manage.py` commands through the CLI:

```bash
orunr django manage makemigrations
orunr django manage migrate
```

## Docker Compose slots

When running multiple checkouts side-by-side, each needs its own **slot** to
avoid port/volume conflicts. The slot is stored in `.doco-slot` (gitignored).

| Slot | Main port (nginx) | Backend (direct) | Postgres | Redis |
|------|-------------------|------------------|----------|-------|
| 0    | 80                | 8000             | 5432     | 6379  |
| 1    | 8081              | 8100             | 5532     | 6479  |
| 2    | 8082              | 8200             | 5632     | 6579  |

Set up once per checkout:

```bash
echo 0 > .doco-slot   # or 1, or 2
```

Slot overlays live in `infra/docker/overlays/slot-{0,1,2}.env`. They define the
`DOCO_*` variables (ports, network/volume names, `DOCO_SITE_DOMAIN`) used both for
compose YAML substitution and for baking values into the split `.env` files.

## Git commit messages

```
feat: add email queue connector
fix: handle empty agent run output
chore: bump deps
wip: in-progress stuff
```
