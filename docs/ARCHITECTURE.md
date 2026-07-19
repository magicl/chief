# Chief — Architecture

High-level backend structure and cross-cutting rules. Feature specs live under
`docs/specs/`; this doc is the stable reference for app boundaries and secrets.

---

## Backend layout

```
backend/
  apps/          # Django apps — domain, HTTP, Celery transport
  libs/          # Django-free packages (providers, tools, algorithms)
  chief/         # Project shell (settings, celery, URLs)
```

**Direction:** apps orchestrate; libs compute. Apps import libs; libs never import
`apps.*`.

| App | Role |
|-----|------|
| `apps.agents` | Agent models, config ingest/materialization, tool wiring |
| `apps.queues` | Agent-scoped queues, sources, items, poll/release tasks |
| `apps.sessions` | Sessions, event log, session services/tasks |
| `apps.runner` | Celery step loop, LLM + tool invocation |
| `apps.bus` | Foundational Redis pub/sub, resource publishers, leases, and mailbox |
| `apps.keys` | Encrypted credentials (system + user) |
| `apps.local_sync` | Finite cross-domain local-provider reconciliation |
| `apps.web` | Dashboard, SSE, control endpoints |

### App dependencies (import direction)

| App | May import from |
|-----|-----------------|
| `apps.agents` | Django/stdlib, `libs.tools`, `libs/agent_spec`, `keys` (via wiring), foundational `bus` publishers, **`queues`** (materialize only) |
| `apps.queues` | Django/stdlib, `libs.sources`, `sessions` (releasable predicate only) |
| `apps.sessions` | `agents`, `bus`, `keys` (resolve in tasks), `libs.algorithms` (tasks only) |
| `apps.bus` | Django/stdlib only; domain-free |
| `apps.runner` | `agents`, `sessions`, `bus`, `keys` (resolve), `libs.providers`, `libs.tools` |
| `apps.keys` | Django/stdlib, `cryptography`, foundational `bus` publishers |
| `apps.local_sync` | `agents`, `keys`, `bus`, `libs.file` |
| `apps.web` | Domain apps and foundational `bus` (keys: metadata + commands only); not `local_sync` |

Import edges point from the importer toward its dependencies. `bus` stays foundational
and domain-free: it imports no domain app, while `agents` and `keys` may import its
publisher helpers. `keys` imports only `bus` among apps. **`apps.agents`** imports
**`apps.queues`** only for config materialization (`sync_from_spec`). `local_sync` is
an outer, cross-domain reconciler that may import `agents`, `keys`, `bus`, and
`libs.file`; no domain app imports `local_sync`. `web` remains the outer HTTP
transport and must not import `resolve_*` from keys.

---

## Three-layer request handling

Views, services, and models form three distinct layers. Every request flows
**view → service → ORM/model**; layers never skip.

### Views (`apps.web`)

Views handle HTTP concerns only: authentication, request parsing, response
rendering. **Views must not contain ORM queries or direct model access.** They
call service functions for all data and mutations.

A view's responsibilities:

- Enforce authentication (`@login_required` or explicit check)
- Enforce ownership (via a service helper that returns the object or raises)
- Parse request parameters
- Call service queries/commands
- Select template and build rendering context
- Return the appropriate HTTP response type

### Services (`apps/<app>/services/`)

Each app exposes a **public API** for views, other apps, and Celery tasks:

| Module | Purpose |
|--------|---------|
| `services/queries.py` | Read-only domain access (no bus publish, no task scheduling) |
| `services/commands.py` | Mutations: DB writes, notifications, downstream `.delay()` |

**Rules:**

- Celery tasks, runner, and web views call **services**, not raw ORM updates
  (when a service exists).
- Tasks are **thin orchestrators**: query → lib function → command.
- Commands that mutate session/agent state emit UI notifications (see Real-time
  notifications below).
- Service functions receive **user ids** or **model instances**, not
  `HttpRequest` objects.
- Ownership checks that multiple views share belong in a service helper (e.g.
  `get_owned_agent(user_id, agent_id)` returns the agent or raises).

### Models

Standard Django models. Business logic lives in services, not on model methods
(except trivial accessors / `__str__`).

### Celery tasks

- Each app that needs async work owns **`apps/<app>/tasks.py`**.
- Register task modules in **`chief/tasks.py`** (imports only — see existing
  `apps.runner.tasks` pattern).
- **`apps.runner.tasks`**: long-lived session execution (`run_session`).
- **`apps.sessions.tasks`**: short metadata side work (e.g. `generate_session_name`).
- Tasks never call `publish_*` directly; commands own side effects.

---

## Real-time UI notifications (SSE)

Session-scoped Redis pub/sub carries an envelope:

- `session_event` — `AgentSessionEvent` payload (dedupe by `seq` in SSE)
- `session_update` — partial session patch, e.g. `{"name": "..."}`

Commands call `publish_session_update` after DB writes. The session detail page
listens on the existing SSE connection and patches Alpine state.

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

**Rules:**

- Do not import `runner` or `web` from `agents` or `sessions`.
- Provider-specific UI (e.g. listing models for dashboard buttons) belongs in
  `web`, not `agents`.
- Types referenced by `AgentConfigSpec` stay in `agents` even when `runner`
  invokes them at runtime.

---

## Libraries (`libs/`)

| Package | Role |
|---------|------|
| `libs/agent_spec` | Pydantic `AgentConfigSpec`, load-time spec migrations, shipped example specs (Django-free) |
| `libs/providers/llm` | LLM provider implementations |
| `libs/providers/key` | Key-provider protocols + disk credential parsing |
| `libs/providers/data` | Data-provider protocols + disk agent parsing |
| `libs/file` | Shared file normalization + content hashing |
| `libs/tools` | Tool definitions + registry |
| `libs/sources` | Source adapter protocol + registry |
| `libs/algorithms` | Reusable algorithms (may call providers) |

Libs stay Django-free. When a lib needs credentials, the **app boundary injects**
callables (`token_supplier`, `secret_supplier`) — libs do not import `apps.keys`.
Disk parsers in `providers/key` and `providers/data` depend on `libs/file`; Django
apps own user lookup and ORM ingest, while `apps.local_sync` owns finite
local-provider reconciliation.

`libs/agent_spec` holds the **config language** only (types, validation, dict
upgrades). It does not touch the database or call other apps. Today this package
lives under `apps/agents/` (`spec.py`, `spec_migrations/`); it moves to
`libs/agent_spec/` as the schema grows (spec 3+).

---

## Agent configuration

**Spec detail:** [`docs/specs/2026-07-03-agent-config-schema/`](specs/2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-design.md)

The **`AgentConfigSpec`** (YAML/JSON) is the declarative definition of an agent.
Postgres holds an immutable **`AgentConfig`** row per revision plus **derived runtime
rows** (triggers, queues, sources, …) that Celery and tools operate on.

Local disk providers ingest user credentials from
`$CHIEF_LOCAL_DIR/keys/*.yaml` and agent configs from
`$CHIEF_LOCAL_DIR/agents/*.yaml` into Postgres. The database remains the runtime
source of truth, and disk-sourced items are read-only in the UI; update their YAML
files instead. Both globs are **non-recursive** in v1: nested directories under
`keys/` and `agents/` are not scanned.

Docker Compose fixes this layout at the repository's `.local/` directory: it reads
`.local/keys/*.yaml` and `.local/agents/*.yaml`, mounting `.local/` at
`/mnt/local` in the backend, worker, and beat containers. `CHIEF_LOCAL_DIR`
remains the generic application setting for non-Compose environments.

Celery Beat enqueues one finite reconciliation task every five seconds; stale Beat
deliveries expire after five seconds. A token-owned Redis lease prevents overlapping
runs. During a run, synchronous progress checkpoints atomically renew the lease and
verify ownership; a failed ownership check raises immediately and the task boundary
logs the run failure.
Each scan reconciles keys before agents because agent materialization may resolve
credentials. Unset `CHIEF_LOCAL_DIR` and a configured but missing root are no-ops.
Postgres remains runtime truth; web and runtime consumers never read provider files
directly. `AppConfig.ready()` performs no local-provider filesystem or ORM work.

### Schema evolution

- **`schema_version`** in JSON mirrors **`AgentConfig.spec_version`** on save.
- **Breaking changes** (rename, remove, semantic change) → new step in
  `libs/agent_spec/migrations/` and bump version.
- **Backward-compatible additions** (new optional fields with defaults, e.g.
  `queues: []`) → **no version bump**; pydantic accepts them on the current version.

### Materialization (spec → runtime)

One orchestrator applies a saved config to the platform. **Entry point:**
`apps.agents.services.commands.persist_agent_config` (alias concept:
`apply_agent_config`).

```mermaid
flowchart LR
  Spec[AgentConfigSpec]
  Persist[persist_agent_config]
  Mat[materialize_agent_config]
  AC[(AgentConfig)]
  Trg[Trigger rows]
  Que[apps.queues.sync_from_spec]

  Spec --> Persist
  Persist --> AC
  Persist --> Mat
  Mat --> Trg
  Mat --> Que
```

**Rules:**

1. **Orchestrator lives in `apps.agents`** — `materialize.py` (or equivalent) runs
   inside the same `@transaction.atomic` as the new `AgentConfig` row.
2. **Each domain owns its slice** — e.g. `apps.queues.commands.sync_from_spec(agent,
   config, spec.queues)` reconciles `Queue` / `Source` DB rows from the optional
   `queues[]` block. Same pattern for future spec-controlled infra.
3. **Implementers do not orchestrate each other** — only `apps.agents` calls the
   full list, in a documented order.
4. **Consumers never materialize** — `runner`, `web`, and Celery tasks use DB state;
   they do not re-sync from spec mid-session (except loading the pinned
   `agent_config` row the session was started with).

**Intentional dependency:** `apps.agents` imports **`apps.queues`** (and later apps
as needed) for materialization only. `apps.queues` does **not** import `apps.agents`
ingest. Direction remains: domain resource apps are leaves relative to the agents
orchestrator.

### What the spec controls

| Spec section | Materialized? | Where |
|--------------|---------------|--------|
| `triggers[]` | Yes | `Trigger` rows (`apps.agents`) |
| `queues[]` (optional) | Yes | `Queue`, `Source` rows (`apps.queues`) |
| `tools[]` | No | Runtime wiring from spec JSON |
| `credential_ref` | No | Resolve at invoke time (`apps.keys`) |

**Queues are agent-scoped:** declared under `queues[]` on the owning agent’s spec
(optional nested `sources[]` per queue). Config save creates/updates stable DB rows
(by queue **id** slug) so Celery poll tasks and items keep a fixed identity. Another
agent may **`put`** into a queue it does not own; only the owning agent’s sessions
**take** from it (see spec 3 / spec 5).

**Schedule and queue triggers** start agent sessions from Celery beat (and, for queue
triggers, immediately after `put_item` when an item is available). A **`schedule`**
trigger gets a **`django-celery-beat`** `PeriodicTask` on config save (UTC crontab per
trigger cron); a **`queue`** trigger is scanned every 15 s plus immediate dispatch on
`put_item`, until `max_sessions` concurrent sessions (default 1) are in flight. Beat also
runs **`poll_active_sources`** every five minutes to enqueue source polling across the
platform. See [`docs/specs/2026-07-05-agent-scheduling/`](specs/2026-07-05-agent-scheduling/2026-07-05-agent-scheduling-design.md).

---

## Queues & sources

**Spec detail:** [`docs/specs/2026-07-04-sources-and-queues/`](specs/2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-design.md)

Platform ingest: sources discover external items → deduped **queue items** → agents
**take** / **complete** / **fail** via the `queue` tool. Queues replace the original
“pipes” concept.

**Attempt history:** when an item is retried across sessions (stale release, explicit
`fail`, worker pool), **`QueueItemAttempt`** records **every** session that took it —
not only the current taker on `QueueItem`. Operators and debug tooling can list all
sessions that tried an item before it reached `done`, `failed`, or `exhausted`.

---

## Credentials & secrets

**Implementation detail:** [`docs/specs/2026-07-03-key-management/`](specs/2026-07-03-key-management/2026-07-03-key-management-design.md)

Architectural rules (all features must follow):

1. **Encrypted store is primary.** Postgres (`apps.keys`); env vars are dev/ops
   fallback for LLM types only when no stored credential exists.
2. **Refs in config, secrets at runtime.** YAML names credentials with
   **`credential_ref`** (LLM block and tool instances) — never embed values.
3. **Versioned agent config.** `AgentConfigSpec` carries **`schema_version`**;
   **`AgentConfig.spec_version`** mirrors it on the row. **Load:** apply the upgrade
   chain in code so any stored version becomes the current in-memory shape. **Save:**
   always persist at the latest version as a **new** config row. Never rewrite spec JSON
   in Django data migrations; no bulk background upgrade.
4. **Write-only for humans.** UI and admin accept secrets; surfaces show **Set / Not
   set** only — no read-back, hints, or prefilled password fields.
5. **Just-in-time for machines.** Resolve immediately before use; do not retain
   plaintext on session state, config objects, or library client fields.
6. **Type-safe wiring.** Every credential has a `type`; consumers declare
   `expected_type` and reject mismatches.
7. **Health-gated resolution.** `UserCredential` tracks `health_status` (`ready` /
   `needs_attention`) and a stable `health_code` (`value_empty`, `oauth_not_connected`,
   `invalid_declaration`, `unknown_type`) independently from lifecycle `status`.
   `resolve_secret` and OAuth authorize/materialize require `status == active` **and**
   `health_status == ready`. Recoverable disk declaration problems (empty static value,
   unconnected OAuth, invalid shape, unregistered type) persist an identifiable
   `needs_attention` row instead of only emitting an ERROR log; unrecoverable
   identity failures (bad YAML, missing owner/name) still fail the sync item and log
   safely. The Keys UI shows the health label in place of Set/Connected and hides
   Authenticate for `invalid_declaration` / `unknown_type` rows — those disk
   declarations are fixed on disk, not by re-authorizing.

**Import boundary:** `apps.web` uses metadata queries + commands only (no `resolve_*`).
`apps.runner`, `apps.agents`, and tasks use `resolve_*` / `make_secret_supplier`.
`apps.keys` remains a domain leaf with no domain app imports; besides Django, stdlib,
and `cryptography`, it may import foundational `apps.bus` publishers for committed
user-resource hints.

### Google OAuth application and grant ownership

Google OAuth providers are selected through the `apps.keys.oauth` provider registry.
The registry owns provider behavior and the catalog of human-facing capability IDs,
descriptions, exact scopes, and `current` or `future` support status. Humans select
those capability IDs; configuration and disk declarations never accept arbitrary
scope URLs.

The OAuth refresh grant is an encrypted grant in `UserCredential.encrypted_value`.
It contains only the provider refresh token and validated granted scopes. Immediately
before a Google operation, `apps.keys` combines that grant with the application
settings and emits a runtime-only envelope. The Django-free Google client consumes
the envelope for that operation; the envelope, client secret, refresh token, and
access tokens are never persisted in agent config or rendered to humans.

For disk-owned OAuth credentials, the YAML disk declaration owns the name, owner,
provider, and capability selection. It never contains the provider grant or OAuth
application values. Edit declaration metadata on disk rather than in the UI; Chief
keeps the resulting grant encrypted in Postgres and clears it when an ownership-
relevant declaration change requires fresh consent.

Production has one structured application secret: `$KNOX/chief/oauth/google`. Its
exact keys map to process environment settings as follows:

- `client_id` → `GOOGLE_OAUTH_CLIENT_ID`
- `client_secret` → `GOOGLE_OAUTH_CLIENT_SECRET`

Chief never reads Knox directly. Deployment tooling materializes those two keys as
environment variables; no Knox client or real values belong in this repository.
Both settings default to blank so installations that do not use Google OAuth still
start normally.

Docker Compose already loads `.env.local` into the backend, worker, and Beat services.
Blank placeholders live under `#[backend]` in `.env.local.example`. Only web
authorization start/callback handling and operation-time OAuth materialization require
the application values. Beat does not use the Google OAuth values. Production secret
scoping is the deployment responsibility.

Register the exact callback URL `https://<origin>/settings/keys/oauth/google/callback/`
for each deployed origin, including the trailing slash. The callback path is fixed by
Chief rather than supplied by a user, and it requires HTTPS outside local development.

`SECURE_PROXY_SSL_HEADER` is safe only at a controlled TLS-termination boundary: the
production Django application port must be network-isolated and unreachable directly,
and the trusted front proxy must overwrite `X-Forwarded-Proto` rather than append to
or pass through a client-supplied value. The current Docker Compose port publishing is
local development only and is not a production exposure template.

Production ingress, access logs, and APM must omit the OAuth callback query string
entirely before the request reaches Django. Application middleware cannot redact logs
already emitted by upstream infrastructure. The backend must remain network-isolated
behind that controlled proxy. The current development nginx keeps `access_log off`.
Django independently applies `Referrer-Policy: no-referrer` and `Cache-Control:
no-store` to every callback response, including converted route failures.

---

## External integrations

**Spec detail:** [Gmail](specs/2026-07-06-gmail-integration/2026-07-06-gmail-integration-design.md) · [ClickUp](specs/2026-07-06-clickup-integration/2026-07-06-clickup-integration-design.md) · [Cloud file metadata](specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-design.md)

Each external service follows the same three-component anatomy:

| Layer | Package | Role |
|-------|---------|------|
| **Client** | `libs/clients/<service>/` | Low-level API wrapper; credentials injected at call time |
| **Source adapter** | `libs/sources/adapters/` | Polls external items → enqueues queue payloads |
| **Tool** | `libs/tools/tools/` | Agent-callable functions gated by `allow` / `deny` |

Current integration components:

| Integration | Client | Tool | Source adapter |
|-------------|--------|------|----------------|
| Gmail | `libs/clients/gmail/` | `libs/tools/tools/gmail.py` | `libs/sources/adapters/gmail.py` |
| ClickUp | `libs/clients/clickup/` | `libs/tools/tools/clickup.py` | `libs/sources/adapters/clickup.py` |
| Google Drive | `libs/clients/google_drive/` | `libs/tools/tools/google_drive.py` | None — interactive metadata tool only |
| Dropbox | `libs/clients/dropbox/` | `libs/tools/tools/dropbox.py` | None — interactive metadata tool only |

**`ToolInstance.config`** / **`SourceSpec.config`** hold non-secret addressing
(mailbox, team id, query filters). Shared connection details can be declared once
under **`integrations[]`** and referenced via **`integration:`** on tools and
sources (schema v3+). Secrets stay in `apps.keys`; YAML references them via
**`credential_ref`** only (on the integration or inline on the tool/source).

**Queue payload envelope:** source adapters enqueue `{data, ref}` — `data` is the
session-facing summary; `ref` carries stable ids and fetch hints so tools can re-read
full content (e.g. attachments) without bloating the queue item.

**Source dedupe (`config.dedupe`, default `true`):** each adapter maps an upstream item
to a queue **`external_id`** (Gmail message id, ClickUp task id). With dedupe on,
`put_item` is idempotent on `(source, external_id)` — the same item is never enqueued
twice, including after it reaches a terminal state (`done` / `failed`). Poll prefetches
known ids per source so adapters can skip expensive fetches. Set **`dedupe: false`** to
derive `external_id` from a change token (Gmail `historyId`, ClickUp `date_updated`) so
updates can re-enter the queue.

**Google service account and delegation:** `type=google` is the canonical credential
type consumed by the Gmail tool, Gmail source adapter, and Google Drive tool. The
integration, tool, and source identifier remains **`gmail`**; only its credential type
changed. Store the shared complete service-account JSON as a `google` credential and set
**`config.subject`** on an integration (or on both the Gmail tool and source) to select
the impersonated mailbox. Enable the Gmail API and/or Drive API as needed.
**Domain-wide delegation is required when Gmail is enabled** and whenever Drive
impersonates a Google Workspace user. It is unnecessary only for non-delegated Drive
access using the service-account identity. In Google Workspace Admin, authorize only the
union of scopes required by enabled tools: Gmail scopes (`gmail.modify`, `gmail.send`)
only when Gmail is enabled, and the Drive scope (`drive.metadata.readonly`) only when
Drive is enabled. Example:
[`backend/libs/agent_spec/examples/gmail-triage.yaml`](../backend/libs/agent_spec/examples/gmail-triage.yaml).

**ClickUp (personal API token):** store the token as a `type=clickup` credential;
set **`config.team_id`** on an integration (or on the tool and source) for workspace
addressing. The `libs/clients/clickup` client wraps the REST API via **`httpx`**; the
source adapter polls a configured **`list_id`** (with optional status filters) into the
queue. Example:
[`backend/libs/agent_spec/examples/clickup-inbox.yaml`](../backend/libs/agent_spec/examples/clickup-inbox.yaml).

**Google Drive and Dropbox metadata tools:** both tools require a non-empty
**`config.roots`** list of operator-approved aliases and expose only `list_roots`,
`list_folder`, `get_metadata`, and `search`. They return metadata, never file content,
and do not expose upload, mutation, sharing, download, preview, or export operations.
Normalized **`web_url`** is nullable: Drive may return its metadata `webViewLink`;
Dropbox always returns null because creating or retrieving a shared link is outside the
approved metadata-only scope.

Drive resolves every configured locator, including the special `file_id: root`, to its
current canonical provider ID before ancestry authorization. Dropbox applies an optional
`config.namespace_id` path root before resolving configured paths, then authorizes using
provider-returned **`path_lower`** segments rather than Python lowercasing or raw string
prefixes. Both clients recheck returned metadata against the selected root. These
integrations intentionally have no source adapters or queue ingestion path. Example:
[`backend/libs/agent_spec/examples/cloud-files-browser.yaml`](../backend/libs/agent_spec/examples/cloud-files-browser.yaml).
