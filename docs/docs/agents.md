# Chief Agent Documentation

Chief agents are YAML-configured LLM sessions that perform routine tasks, triggered
manually, on a schedule, or in response to queue items. This document covers the
agent file format, LLM providers and models, available tools, triggers, queues,
integrations, and credentials.

For working examples, see [`backend/libs/agent_spec/examples/`](../../backend/libs/agent_spec/examples/).

---

## Agent file format

With Docker Compose, place agent files in `.local/agents/*.yaml` and key files in
`.local/keys/*.yaml`. Compose mounts `.local/` at `/mnt/local` in each consuming
container. `CHIEF_LOCAL_DIR` remains the generic application setting for
non-Compose environments.

`.local/` is gitignored and may be a symlink to a private git checkout (for
example `chief-private`) so personal agents, keys, and eval fixtures stay
versioned without landing in the public repo. Layout:

```text
.local/                    # or $CHIEF_LOCAL_DIR
  agents/*.yaml
  keys/*.yaml              # keep secrets out of git even in a private remote
  evals/inbox/scenarios/   # optional private inbox eval scenarios
```

Inbox evals load public scenarios from `evals/inbox/scenarios/` and, when
present, merge `$CHIEF_LOCAL_DIR/evals/inbox/scenarios/` (host-side fallback:
repo `.local/evals/inbox/scenarios/`). Duplicate scenario `id` values across
the two trees fail the suite loudly.

An agent is a single YAML file with two layers:

1. **Envelope** — metadata fields that identify the agent on disk.
2. **Config body** — the `AgentConfigSpec` validated by Pydantic.

### Envelope fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `owner` | yes | — | Owner identifier (exact username or unique email) |
| `identifier` | no | filename stem | Unique agent id within the owner scope |
| `name` | no | same as `identifier` | Human-readable display name |

The envelope is stripped before the config body is validated. All remaining
fields form the `AgentConfigSpec`.

### Config body (`AgentConfigSpec`)

```yaml
schema_version: 4
description: Optional human description
llm:
  provider: openai          # "openai", "anthropic", "local_openai", "repeat"
  model: gpt-5.4-mini
  temperature: 0.7          # optional
  credential_ref: my-key    # optional; falls back to env vars
system_prompt: |
  You are a helpful assistant.
limits: {}          # optional per-session hard limits
integrations: []   # shared connection configs (see Integrations)
triggers: []       # how the agent is activated (see Triggers)
tools: []          # tool instances available to the LLM (see Tools)
queues: []         # work queues with optional sources (see Queues)
skills: []         # prompt blocks loaded on demand (see Skills)
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `4` (literal) | yes | Must match `AGENT_CONFIG_SPEC_VERSION` |
| `description` | string | no | Human-readable purpose of the agent |
| `llm` | `LLMSpec` | yes | LLM provider and model configuration |
| `system_prompt` | string | yes | System prompt injected at session start |
| `limits` | `SessionLimitsSpec` | no | Per-session hard limits |
| `integrations` | list of `IntegrationSpec` | no | Shared connection details |
| `triggers` | list of `TriggerSpec` | no | Activation rules |
| `tools` | list of `ToolInstance` | no | Tool instances the LLM can call |
| `queues` | list of `QueueSpec` | no | Agent-scoped work queues |
| `skills` | list of `SkillSpec` | no | Named prompt blocks available on demand |

An optional key written with no value (`integrations:` on its own line) parses as null and is
treated the same as leaving the key out — the field falls back to its default. This applies to
nested keys too, such as a tool's `config:` or a queue's `sources:`. Fields that are documented
as nullable, such as `credential_ref` and `max_sessions`, keep the explicit null and its meaning.

A tool's `allow:` is the one exception: leaving it valueless is rejected rather than falling back
to the permissive `['*']` default, so a malformed permission list cannot silently grant every
function. See [Allow / deny gating](#allow--deny-gating).

---

## LLM

`llm` selects the backend provider and model for every session of the agent.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | yes | Registered provider id (see below) |
| `model` | string | yes | Model id for that provider |
| `temperature` | float | no | Sampling temperature when the model supports it |
| `credential_ref` | string | no | Named API key; falls back to env vars when omitted |

### Providers

Registered in `libs.providers.llm.registry.PROVIDERS`:

| Provider | Purpose | Credential |
|----------|---------|------------|
| `openai` | OpenAI Chat Completions | `openai` key or `OPENAI_API_KEY` |
| `anthropic` | Anthropic Messages API | `anthropic` key or `ANTHROPIC_API_KEY` |
| `local_openai` | OpenAI-compatible local server (default host `localhost:11434`) | optional `LOCAL_OPENAI_API_KEY` (defaults to `local`) |
| `repeat` | Test provider that echoes the latest user message | none |

Unknown `provider` values fail at session start. Model ids are not schema-validated;
any string is accepted, but only the models below have known pricing / catalog
entries used by the config editor helper.

### Models

**OpenAI** (`provider: openai`)

| Model | Notes |
|-------|-------|
| `gpt-5.5` | Does not support `temperature` |
| `gpt-5.4-mini` | Default in most examples |
| `gpt-5.4-nano` | |

**Anthropic** (`provider: anthropic`)

| Model | Notes |
|-------|-------|
| `claude-opus-4-8` | |
| `claude-sonnet-4-6` | |
| `claude-haiku-4-5` | Preferred id; `claude-haiku-4.5` is accepted and normalized to this |

**Local OpenAI** (`provider: local_openai`)

| Model | Notes |
|-------|-------|
| `llama3.2` | Cost is estimated from power draw × latency, not per-token rates |

**Repeat** (`provider: repeat`) has no catalog models; any `model` string works for
tests (examples often use `repeat`).

---

## Session limits

Agent-level limits apply to every session unless a lower server or trigger cap wins:

```yaml
limits:
  max_iterations: 50
  max_cost_usd: 2.00
```

| Field | Type | Description |
|-------|------|-------------|
| `max_iterations` | int | Optional iteration cap; must be at least `1` |
| `max_cost_usd` | decimal | Optional positive cost cap in USD |

Chief uses the lowest configured cap at the server, agent, and trigger levels.

---

## Triggers

A trigger defines how an agent session starts.

```yaml
triggers:
  - name: manual
    kind: manual
  - name: daily-check
    kind: schedule
    cron: "0 8 * * *"
    prompt: Run the daily check.
    max_sessions: 1
  - name: inbox-worker
    kind: queue
    queue: inbox
    prompt: Process the next item.
    max_sessions: 2
  - name: triage
    kind: button
    button_text: Triage inbox
    prompt: Triage the inbox now.
```

### Trigger kinds

| Kind | Description | Required fields |
|------|-------------|-----------------|
| `manual` | User-initiated; no automatic scheduling | — |
| `schedule` | Cron-based periodic execution | `cron`, `prompt` |
| `queue` | Fires when items appear on a named queue | `queue`, `prompt` |
| `agent` | Reserved schema kind; no runtime dispatcher is currently implemented | `prompt` |
| `button` | User-initiated quick action with a labeled button in the chat UI | `button_text`, `prompt` |

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique trigger name within the agent |
| `kind` | enum | One of `manual`, `schedule`, `queue`, `agent`, `button` |
| `cron` | string | Cron expression (required for `schedule`) |
| `queue` | string | Queue id from `queues[]` (required for `queue` kind) |
| `button_text` | string | Short button label, max 40 characters (required for `button`) |
| `prompt` | string | Injected user-message at session start (required unless `manual`) |
| `max_sessions` | int | Max concurrent sessions; defaults to `1` for schedule/queue, `null` for manual and button |
| `max_iterations` | int | Optional per-session iteration cap; must be at least `1` |
| `max_cost_usd` | decimal | Optional positive per-session cost cap in USD |
| `blocks` | list of block conditions | Optional gates that must all pass before a session starts (see Block conditions) |

Trigger limits only narrow the agent-level `limits` and server defaults. Chief uses
the lowest configured value at the global, agent, and trigger levels.

### Block conditions

`blocks` holds conditions that must pass before Chief starts a session for the
trigger. Omitting `blocks` — or setting it to `[]` — keeps the trigger's existing
behavior, so configs written before this field keep working unchanged.

```yaml
tools:
  - id: journal-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: journal

queues:
  - id: journal

triggers:
  - name: journal-worker
    kind: queue
    queue: journal
    prompt: Process the next journal item.
    blocks:
      - kind: tool_ready
        tool: journal-vault
```

Semantics:

- **Ordered AND.** Every entry must report ready. Chief evaluates entries in list
  order and short-circuits on the first one that is not ready, so put the cheapest
  or most likely blocker first.
- **Fail closed.** An unknown condition handler, a failed probe, or a timeout counts
  as not ready; the trigger does not start a session.
- **Blocked dispatch is a skip, not a failure.** A blocked `queue` trigger leaves its
  items available and retries on the next dispatch; a blocked `schedule` trigger skips
  that cron tick without catching up; blocked `manual` and `button` starts report the
  block reason instead of creating a session.

#### `tool_ready`

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `tool_ready` | Required condition kind |
| `tool` | string | Required `tools[].id` declared on this same agent |

`tool_ready` waits until the named tool instance reports that it can support a new
session. Most tools are always ready; the `obsidian` tool is ready only once its vault
service reports that the initial sync finished, which keeps a journal or notes agent
from working against an empty vault.

The config editor's **Add trigger** helper automatically inserts one `tool_ready`
condition per readiness-reporting tool already present in the document when it adds
a `queue` or `schedule` trigger. The helper preserves `tools[]` order. Manual, button,
and agent triggers remain ungated, tools added later do not backfill existing triggers,
and editing or removing conditions remains YAML-only.

The reference is strict: `tool` must match a `tools[].id` on the same agent, unknown
`kind` values are rejected, and unknown fields inside a condition are rejected. All
three are config validation failures at save or disk-sync time rather than conditions
that silently never block.

---

## Tools

Tools are namespaced sets of functions exposed to the LLM during a session.
Each tool instance in the config references a tool `type` and optionally restricts
which functions the agent may call.

### Tool instance spec

```yaml
tools:
  - id: gmail-personal
    type: gmail
    integration: gmail-personal     # optional; inherits type/credential/config
    credential_ref: gmail-personal  # optional; overrides integration
    config:                         # optional; per-instance addressing
      subject: me@example.com
    allow: [list, read, label, archive]
    deny: [send, trash]
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | — | Instance id (lowercase slug, max 64 chars) |
| `type` | string | — | Tool type (e.g. `clock`, `gmail`, `google_drive`, `dropbox`, `clickup`, `queue`) |
| `integration` | string | `null` | References an `integrations[].id` for shared config |
| `credential_ref` | string | `null` | Key name for credential lookup |
| `config` | object | `{}` | Non-secret per-instance configuration |
| `allow` | list | `['*']` | Function names the agent may call (`*` = all) |
| `deny` | list | `[]` | Function names blocked even if allowed |

### Allow / deny gating

The runner checks `allow` and `deny` before dispatching any function call:

- If `allow` contains `'*'`, all functions are permitted unless in `deny`.
- If `allow` is an explicit list, only those functions are permitted.
- `deny` always wins over `allow`.

Omit `allow` entirely to accept the permissive default. Writing the key with no value is
rejected, so an unfinished edit fails validation instead of granting every function.

### Built-in tools

When `skills[]` is non-empty, Chief automatically exposes `load_skill.load`; do not
declare it in `tools[]`. The function description lists the configured skill ids and
descriptions, and calling it returns the selected skill's full content.

#### `clock`

Read-only UTC time. No credentials required. No `config` fields.

| Function | Description | Parameters |
|----------|-------------|------------|
| `now` | Return current UTC time as ISO-8601 | — |

#### `gmail`

Gmail operations. Requires a `google` credential using either Google OAuth or a
complete service-account JSON value. For OAuth, `gmail_read` permits read-only
operations, `gmail_modify` permits reading and mailbox changes (including sending),
and `gmail_send` permits sending only. Configure tool `allow`/`deny` to expose only
operations authorized by the selected capabilities. The tool/integration/source type
remains `gmail`; `gmail` is not a credential type.

**Config fields** (tool or integration `config`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `subject` | string | no | — | Workspace user to impersonate via domain-wide delegation (service accounts). Omit for OAuth (uses the connected account / `me`). |

| Function | Description | Readonly |
|----------|-------------|----------|
| `list` | Search by Gmail query; returns `message_ids` and nullable `next_page_token` | yes |
| `read` | Return one decoded, compact message with bounded body and attachments | yes |
| `list_labels` | Return compact `id`, `name`, and optional `type` records under `labels` | yes |
| `get_attachment` | Return decoded bytes as standard base64 in `data_base64` | yes |
| `label` | Add/remove labels; returns a compact message acknowledgement | no |
| `archive` | Remove INBOX; returns a compact message acknowledgement | no |
| `mark_spam` | Move to spam; returns a compact message acknowledgement | no |
| `trash` | Move to trash; returns a compact message acknowledgement | no |
| `send` | Send a message; returns a compact message acknowledgement | no |

Typical deny pattern: `deny: [send, trash]` — restrict destructive operations.

`read` returns the projected summary fields `id`, `thread_id`, `label_ids`, `from`,
`to`, `cc`, `reply_to`, `return_path`, `subject`, `message_id`, `date`,
`received_at`, and `snippet` when available. It always includes:

- `has_attachments`; `attachments[]` records with `attachment_id`, `filename`,
  `mime_type`, and nullable `size`; and `attachments_meta` with `truncated`,
  `included`, `total`, and `omitted_count`. At most 25 attachments are returned.
- `authentication`: `spf {verdict, domain}`, `dkim[] {verdict, domain}`,
  `dmarc {verdict, policy, header_from}`, `arc {verdict}`, and
  `alignment {from_domain, reply_to_domain, return_path_domain,
  from_matches_reply_to, from_matches_return_path}`. Missing or untrusted evidence
  produces `unknown`/`null`, not raw authentication headers.
- `advisories[] {code, message}` for safely omitted MIME content.
- `body {text, source}`, where `source` is `plain` or `html_to_text`. Text is limited
  to 32,000 characters; overflow adds
  `body_truncation {truncated: true, omitted_chars, ref}` with the Gmail message
  fetch reference.

`list` is passed through from the client because that client method already returns
only `{message_ids, next_page_token}`. For `get_attachment`, the client decodes
Gmail's base64url transport data to bytes and enforces the 10 MiB limit; the
projection returns exactly `attachment_id`, decoded byte `size`, nullable `mime_type`,
and padded standard-base64 `data_base64`. Gmail mutation successes return
`{ok: true, message_id, label_ids?}`. Successful reads, labels, and attachment
downloads return their compact projection, while `list` returns its compact client
result; none adds `ok`. Failures return
`{ok: false, error: {kind, message}}`, where `kind` is `auth`, `not_found`, or `api`.
There is no raw-result option.

#### `google_drive`

Read-only, metadata-only navigation and search within explicitly configured Google Drive
roots. Requires a `google` credential.

**Config fields** (tool or integration `config`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `roots` | list | yes | — | Non-empty list of aliased Drive roots (see root fields below) |
| `subject` | string | no | — | Workspace user to impersonate via domain-wide delegation (service accounts). Omit for OAuth |

**Root object fields** (`config.roots[]`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | yes | — | Operator-chosen alias used as `root` in every tool call |
| `file_id` | string | yes | — | Drive file/folder id (`root` is allowed and resolved to the canonical My Drive root) |
| `corpus` | `user` \| `drive` | no | `user` | Search corpus; `drive` requires `drive_id` |
| `drive_id` | string | no | — | Shared Drive id; when set, forces `corpus: drive` (cannot combine with explicit `corpus: user`) |

Root aliases and `file_id` values must each be unique within the list.

| Function | Parameters | Description | Readonly |
|----------|------------|-------------|----------|
| `list_roots()` | none | Return metadata for configured roots | yes |
| `list_folder(root, folder_ref?, cursor?, max_results=50)` | `root` required; others optional; `max_results` integer 1–100 | List one level of direct child metadata | yes |
| `get_metadata(root, item_ref)` | `root` and `item_ref` required | Return metadata for one item proven beneath a root | yes |
| `search(root, query, kinds?, cursor?, max_results=50)` | `root` and `query` required; others optional; `kinds` contains `file` and/or `folder`; `max_results` integer 1–100 | Run bounded Drive name/full-text metadata search within a root | yes |

#### `dropbox`

Read-only, metadata-only navigation and search within explicitly configured Dropbox
roots. Requires a `dropbox` credential.

**Config fields** (tool or integration `config`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `roots` | list | yes | — | Non-empty list of aliased absolute Dropbox roots (see root fields below) |
| `namespace_id` | string | no | — | Team-space namespace id selected before path resolution |

**Root object fields** (`config.roots[]`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | yes | — | Operator-chosen alias used as `root` in every tool call |
| `path` | string | yes | — | Absolute Dropbox path starting with `/` (e.g. `/Projects`; `/` for account root). No trailing `/` (except root), and no `.` / `..` segments |

Root aliases and paths must each be unique within the list (paths compared case-insensitively for ASCII).

| Function | Parameters | Description | Readonly |
|----------|------------|-------------|----------|
| `list_roots()` | none | Return metadata for configured roots | yes |
| `list_folder(root, folder_ref?, cursor?, max_results=50)` | `root` required; others optional; `max_results` integer 1–100 | List one level of direct child metadata | yes |
| `get_metadata(root, item_ref)` | `root` and `item_ref` required | Return metadata for one item proven beneath a root | yes |
| `search(root, query, kinds?, cursor?, max_results=50)` | `root` and `query` required; others optional; `kinds` contains `file` and/or `folder`; `max_results` integer 1–100 | Run bounded Dropbox metadata search within a root | yes |

#### Cloud metadata tool contract

Example integrations using the fields above:

```yaml
integrations:
  - id: work-google
    type: google_drive
    credential_ref: work-google
    config:
      subject: agent@example.com
      roots:
        - {id: my-drive, file_id: root}
        - {id: company, file_id: shared-drive-root-id, drive_id: shared-drive-id}
  - id: team-dropbox
    type: dropbox
    credential_ref: team-dropbox
    config:
      namespace_id: optional-team-namespace-id
      roots:
        - {id: projects, path: /Projects}
```

Google Drive resolves the configured locator—including `file_id: root`—to its
canonical current provider ID before checking ancestry. Dropbox authorization uses
provider-authoritative `path_lower` segments, so sibling prefixes such as `/Projects2`
do not pass for `/Projects`.

`root` is always a configured alias. `folder_ref` and `item_ref` are opaque,
provider-specific references returned by prior results; omitted `folder_ref` means the
selected root. Folder listing is one level only, and search and provider page scanning
are bounded.

Pagination cursors are opaque, unsigned validation envelopes around provider state.
They bind the tool instance, root and locator, operation, query/kinds, and selected
folder where applicable. They prevent accidental cross-call reuse but are not
authentication or authorization tokens; each resumed call independently re-resolves
and rechecks current provider metadata against the configured root.

`get_metadata` returns `{"item": <metadata>}`. List and search calls return
`{"items": [...], "next_cursor": <string-or-null>}`. Every normalized item has:

| Field | Description |
|-------|-------------|
| `provider` | `google_drive` or `dropbox` |
| `root` | Configured root alias |
| `id`, `name`, `kind` | Provider reference, display name, and `file`/`folder` (Drive can also return `shortcut`) |
| `mime_type`, `size`, `modified_at` | Nullable metadata fields |
| `parent_refs` | Provider parent references |
| `path` | Dropbox display path; usually null for Drive |
| `web_url` | Nullable Drive metadata link; always null for Dropbox |
| `provider_metadata` | Small provider-specific metadata such as Drive ID or Dropbox revision |

These tools never read, download, export, preview, or return file content. They expose
no upload, edit, move, delete, share, permission, or other mutation operation. Dropbox
does not create or retrieve shared links; `web_url` remains null unless a future
non-metadata feature is separately approved. Neither integration has a source adapter.

#### `clickup`

ClickUp task management. Requires a `clickup` credential (API token).

**Config fields** (tool or integration `config`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `team_id` | string | no* | — | Workspace id used as the default for `list_spaces` (*required for that function unless passed as an argument) |
| `base_url` | string | no | `https://api.clickup.com/api/v2` | ClickUp API base URL (mainly for tests) |

| Function | Description | Readonly |
|----------|-------------|----------|
| `list_spaces` | Return compact spaces in a workspace | yes |
| `list_lists` | Return compact lists in a space | yes |
| `list_tasks` | Return compact task summaries and optional `last_page` | yes |
| `get_task` | Return one bounded normalized task plus recent comments | yes |
| `create_task` | Create a task; returns a compact task acknowledgement | no |
| `update_task` | Update task fields; returns a compact task acknowledgement | no |
| `create_comment` | Add a comment; acknowledges the affected task | no |
| `delete_task` | Delete a task; acknowledges the affected task with `deleted: true` | no |

Typical deny pattern: `deny: [delete_task]`.

Collection results are recursively allowlisted:

- `list_spaces` returns `spaces[] {id, name, archived}`.
- `list_lists` returns `lists[] {id, name, archived, space_id?, folder_id?}`.
- `list_tasks` returns `tasks[]` summaries with exactly `id`, nullable `custom_id`,
  `name`, nullable `status`, `assignees[]`, nullable `priority`, nullable
  `due_date`, nullable `url`, and nullable `date_updated`; `last_page` is present
  only when ClickUp supplies a boolean. A person is `{id, display_name, email?}` and
  priority is `{id, priority}` with nullable values.

`get_task` includes those same summary fields plus `description`,
`markdown_description`, `location`, `creator`, `watchers`, `mentions`, `tags`,
`start_date`, `time_estimate`, `points`, `custom_fields`, `parent`, `dependencies`,
`linked_tasks`, `checklists`, `attachments`, `subtasks`, `comments`, their metadata,
and `advisories`. Plain and Markdown descriptions are each limited to 32,000
characters; overflow adds `{truncated: true, omitted_chars}` under
`description_truncation` or `markdown_description_truncation`. Attachments and
subtasks are each limited to 25. Comments are the 10 most recent comments from
ClickUp's fetched page, each with `id`, `text`, nullable `date`, nullable `user`, and
text limited to 4,000 characters; overflow adds
`text_truncation {truncated: true, omitted_chars}`.

Each `attachments_meta`, `subtasks_meta`, and `comments_meta` is
`{included, total, truncated, omitted_count}`. `total` is null when the provider
does not supply one; `omitted_count` still reports locally observed omissions. If
the optional comments request fails, the task still returns with `comments: []`,
`comments_meta: {included: 0, total: null, truncated: false, omitted_count: 0}`, and
`advisories: [{code: "comments_unavailable", message: "Comments could not be loaded."}]`.

ClickUp mutation successes return `{ok: true, task_id}` plus only applicable `url`,
`deleted`, `status`, or `name`. Successful reads and lists are the projection itself
and do not add `ok`; failures return `{ok: false, error: {kind, message}}`, where
`kind` is `auth`, `not_found`, or `api`. There is no raw-result option.

#### `obsidian`

Root-scoped `list` / `read` / `write` / `append` access to one Obsidian Sync vault,
plus readonly `status`. Requires an `obsidian` credential (Obsidian Sync auth
token). The vault itself is
kept in sync by a separate `services/obsidian` process — see
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — not by `apps.keys` or the Chief
backend directly.

**Config fields** (tool or integration `config`):

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `vault` | string | yes | — | Remote Sync vault id/name known to the vault service |
| `roots` | list of string | yes | — | Non-empty list of allowed vault-relative path prefixes |

| Function | Description | Readonly |
|----------|-------------|----------|
| `list` | List direct child entry names under a vault directory | yes |
| `read` | Read the UTF-8 text content of one vault file | yes |
| `write` | Create or overwrite one vault file with new content | no |
| `append` | Append content to one vault file, creating it (and parent dirs) if missing | no |
| `status` | Report first-sync readiness and whether continuous headless Sync is alive | yes |

Every path must resolve within one of the tool instance's configured `roots`
(enforced server-side by the vault service). The **initial full Sync** remains
the readiness boundary: completion publishes `.sync-ready` and makes
`status.ready` true. Gate session start with a trigger `blocks` entry
`kind: tool_ready` and `tool: <instance id>` — see the complete example under
[Block conditions](#block-conditions). `status` returns only `ready`,
`initial_sync_complete`, and `sync_process_alive` (Chief first-sync and
process liveness, not `ob sync-status` and not a “caught up with Obsidian
Sync” indicator); `ready: false` is a successful observation.

Once first Sync has started, `list` and `read` may expose a partial,
concurrently changing checkout. A leftover checkout after a Sync timeout is
still readable. Missing paths remain `not_found`, and root enforcement always
applies. If Sync never started or the vault was stopped, `list` and `read`
return `sync_pending`. A hard `ob` failure returns `unavailable`.

`write` and `append` require the first full Sync. They return `sync_pending`
while Sync is still in progress, after a timeout leftover, or if Sync never
started or was stopped, and `unavailable` after a hard `ob` failure. There is
no tool retry or sleep; the first typed result is returned. The HTTP request
can still wait up to its configured timeout if the vault service hangs. A
higher-level caller may still treat `sync_pending` / `unavailable` as
transient. Results use the shared `{ok, …}` /
typed `{ok: false, error: {kind, message}}` shape, where `kind` includes
`auth`, `forbidden`, `outside_root`, `not_found`, `sync_pending`,
`unavailable`, and `config`.

The Chief backend never sends the vault service's inter-service URL or auth
token through `apps.keys`. Compose injects `OBSIDIAN_VAULT_URL` and a
well-known local `OBSIDIAN_VAULT_TOKEN`; hosted deployments materialize the
token from `$KNOX/chief/{cluster}/obsidian-vault.txt` as
`OBSIDIAN_VAULT_TOKEN_FILE`. Only the Obsidian
Sync credential itself (`credential_ref`) lives in `apps.keys`. Vault
ensure/release and the restart-recovery snapshot API live in `apps.obsidian`
(registered on the generic agent lifecycle hooks), not in `apps.agents`.

#### `queue`

Agent-scoped queue operations. No external credential — bound to the session's
agent and session ids at runtime. No `config` fields.

| Function | Description | Readonly |
|----------|-------------|----------|
| `list` | List queue ids on this agent | yes |
| `put` | Enqueue a payload | no |
| `take` | Claim the next available item | yes |
| `complete` | Mark a taken item as completed | no |
| `fail` | Mark a taken item as failed | no |

---

## Queues and sources

Queues are agent-scoped work buffers. Items enter via **source adapters** (external
polling) or via the `queue.put` tool (in-session enqueue). A `queue` trigger fires
agent sessions when items are available.

### Queue spec

```yaml
queues:
  - id: inbox
    max_attempts: 3
    min_hold_seconds: 60
    early_release_seconds: 300
    long_hold_seconds: 3600
    sources:
      - id: gmail-main
        type: gmail
        integration: gmail-personal
        config:
          query: "in:inbox -label:x-act"
          max_results: 25
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | — | Queue id (lowercase slug, max 64 chars) |
| `max_attempts` | int | `3` | Max delivery attempts before dead-lettering |
| `min_hold_seconds` | int | `60` | Minimum hold time before re-delivery |
| `early_release_seconds` | int | `300` | Hold time for early release |
| `long_hold_seconds` | int | `3600` | Maximum hold time |
| `sources` | list | `[]` | Source adapters that feed items into the queue |

Hold seconds must satisfy: `min_hold <= early_release <= long_hold`.

### Source spec

A source polls an external system and enqueues items with deduplication.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Source id (unique within the queue) |
| `type` | string | Adapter type (e.g. `gmail`, `clickup`, `test`) |
| `integration` | string | Optional integration id for shared config |
| `credential_ref` | string | Optional credential (overrides integration) |
| `config` | object | Adapter-specific settings (see per-type tables below) |

### Shared source config

All source adapters accept:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dedupe` | bool | no | `true` | When `true`, skip items already known by stable `external_id`. When `false`, Gmail/ClickUp derive `external_id` from a change token (`historyId` / `date_updated`) so updates can re-enter the queue; the `test` adapter still uses `prefix-N` ids and only disables skip-known behavior |

Gmail and ClickUp sources enqueue `{data, ref}`. `data` uses the same projected
summary field names and semantics as Gmail `read` metadata and ClickUp
`list_tasks().tasks[]`, respectively; it never contains raw provider payloads.
`ref` is the stable fetch hint
`{service: "gmail", resource_type: "message", resource_id: <message-id>}` or
`{service: "clickup", resource_type: "task", resource_id: <task-id>}` for a later
tool read.

### Source types

#### `gmail` source

Polls Gmail by search query. Requires a `google` credential.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | yes | — | Gmail search query (e.g. `in:inbox -label:x-act`) |
| `subject` | string | no | — | Workspace user to impersonate (service accounts). Omit for OAuth |
| `max_results` | int | no | `25` | Max messages to fetch per poll (must be ≥ 1) |
| `include_body` | bool | no | `false` | When `true`, copy the projected Gmail `snippet` to `data.body_preview`, limiting it to 2,000 characters plus `…` when shortened |
| `dedupe` | bool | no | `true` | See shared source config |

#### `clickup` source

Polls tasks from one ClickUp list. Requires a `clickup` credential.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `list_id` | string | yes | — | ClickUp list id to poll |
| `statuses` | list of string | no | `[]` | When non-empty, only enqueue tasks in these statuses |
| `max_results` | int | no | `50` | Max tasks to fetch per poll (must be ≥ 1) |
| `include_closed` | bool | no | `false` | Passed through to the ClickUp list API (`include_closed`); not type-checked by the source validator |
| `base_url` | string | no | `https://api.clickup.com/api/v2` | ClickUp API base URL (mainly for tests) |
| `dedupe` | bool | no | `true` | See shared source config |

#### `test` source

Synthetic items for local development and automated tests. No credential.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prefix` | string | no | `test` | Prefix for generated `external_id` values |
| `batch_size` | int | no | `1` | Number of synthetic items to enqueue per poll (must be ≥ 1) |
| `dedupe` | bool | no | `true` | See shared source config |

---

## Integrations

Integrations declare shared connection details that multiple tools and sources
can reference by id. This avoids repeating credential and config blocks.

```yaml
integrations:
  - id: gmail-personal
    type: gmail
    credential_ref: gmail-personal
    config:
      subject: me@example.com
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique integration id |
| `type` | string | Integration type (matches tool/source type) |
| `credential_ref` | string | Credential key name |
| `config` | object | Shared non-secret configuration — same type-specific keys as the matching tool/source (see Tools and Queues and sources) |

When a tool or source sets `integration: <id>`:
- `type` is inherited (must match if explicitly set on the tool/source).
- `credential_ref` is inherited unless the tool/source overrides or explicitly nulls it.
- `config` is merged (tool/source config wins on key conflicts).

Type-specific `config` keys by integration `type`:

| Type | Documented under | Typical shared keys |
|------|------------------|---------------------|
| `gmail` | Tools → `gmail` / Sources → `gmail` | `subject` (plus source-only `query`, `max_results`, …) |
| `google_drive` | Tools → `google_drive` | `subject`, `roots` |
| `dropbox` | Tools → `dropbox` | `namespace_id`, `roots` |
| `clickup` | Tools → `clickup` / Sources → `clickup` | `team_id` / `list_id`, `base_url` |
| `obsidian` | Tools → `obsidian` | `vault`, `roots` |

---

## Skills

Skills are named prompt blocks that an agent can discover and load only when relevant:

```yaml
skills:
  - id: response-style
    description: How to format customer-facing replies
    content: |
      Keep replies concise.
      End with the next action the customer should take.
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique lowercase slug, max 64 characters |
| `description` | string | Non-empty summary included in the `load_skill` tool description |
| `content` | string | Non-empty instructions returned by `load_skill.load` |

Chief adds the `load_skill` auto-tool only when at least one skill is configured.

---

## Credentials

Credentials supply static secrets or OAuth grants to LLM providers, tools, and
sources without embedding them in agent YAML.

See [OAuth Application Setup](oauth-apps.md) to create the Google or Dropbox
application credentials used by Chief.

### Credential references

| Context | Field | Description |
|---------|-------|-------------|
| LLM | `llm.credential_ref` | Provider API key; falls back to env vars if omitted |
| Tool | `tools[].credential_ref` | Per-tool credential; inherits from integration |
| Source | `sources[].credential_ref` | Per-source credential; inherits from integration |
| Integration | `integrations[].credential_ref` | Shared credential for all referencing tools/sources |

### Key file format

Under Docker Compose, key files live in `.local/keys/*.yaml` (mapped to
`/mnt/local/keys/*.yaml`); agent files live in `.local/agents/*.yaml` (mapped to
`/mnt/local/agents/*.yaml`). Outside Compose, both locations derive from the
generic `CHIEF_LOCAL_DIR` application setting. Each key is a YAML file:

Static credentials use `value`:

```yaml
name: my-openai-key
type: openai
owner: your-username
value: sk-...
```

OAuth credentials use `source: oauth` and capability ids in `scopes`; they never
contain a provider grant, refresh token, OAuth application secret, or raw scope URL:

```yaml
name: work-google
type: google
owner: user@example.com
source: oauth
scopes:
  - gmail_read
  - drive_metadata
```

```yaml
name: team-dropbox
type: dropbox
owner: user@example.com
source: oauth
scopes:
  - files_metadata
```

`value` and `source`/`scopes` are mutually exclusive. Static declarations require
the `value` key, although its string may be explicitly empty. OAuth declarations
require `source: oauth` and at least one capability id.

| Field | Form | Required | Description |
|-------|------|----------|-------------|
| `name` | both | no | Key name used by `credential_ref`; defaults to the filename stem |
| `type` | both | yes | Credential type (`openai`, `anthropic`, `google`, `dropbox`, `clickup`, etc.) |
| `owner` | both | yes | Owner username or unique email |
| `value` | static | yes | Static secret string |
| `source` | OAuth | yes | Must be `oauth` |
| `scopes` | OAuth | yes | Non-empty list of provider capability ids, not raw OAuth scope URLs |

Google and Dropbox are the currently registered OAuth providers. Google's
tool-backed capability ids are `gmail_read`, `gmail_modify`, `gmail_send`, and
`drive_metadata`; `drive_metadata` authorizes the read-only Google Drive metadata
tool. Dropbox has one capability id, `files_metadata`, which authorizes the
read-only Dropbox metadata tool (`list_roots`, `list_folder`, `get_metadata`,
`search`). After adding an OAuth declaration, use the Keys page to Authenticate (or
Reauthenticate) the account. Chief stores the resulting grant encrypted in Postgres
rather than writing it to disk. Changing the normalized capability set clears an
existing grant, while changing from OAuth to a valid static declaration replaces the
grant with the static value.
An invalid declaration (unrecognized fields, missing scopes, or an unregistered
type) still creates or updates an identifiable row so it shows up on the Keys page,
but it is flagged with a durable health code and cannot be resolved, reconnected, or
reauthorized until the YAML is fixed on disk; any prior grant or value on that row is
preserved untouched in the meantime. Stable health codes:

| Code | Meaning |
|------|---------|
| `value_empty` | Static declaration with an empty secret string |
| `oauth_not_connected` | Valid OAuth declaration without an encrypted grant |
| `invalid_declaration` | Identifiable YAML that is not a valid static/OAuth shape |
| `unknown_type` | Identifiable YAML whose `type` is not registered |

`auth_kind` is not a disk YAML field; its presence makes the declaration invalid.

Google Drive and Gmail also accept a static `google` credential whose `value` is the
complete service-account key JSON:

```json
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "<complete PEM private key from the downloaded JSON>",
  "client_email": "...@....iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "...",
  "universe_domain": "googleapis.com"
}
```

For service-account credentials, enable the Gmail API and/or Drive API for the
service-account project. Domain-wide delegation is required whenever Gmail is enabled
and whenever Drive uses `config.subject` to impersonate a Workspace user. It is not
required for Drive using the service-account identity directly. In Workspace Admin,
authorize only the union of scopes needed by enabled tools:

- Gmail: `https://www.googleapis.com/auth/gmail.modify` and
  `https://www.googleapis.com/auth/gmail.send`
- Drive: `https://www.googleapis.com/auth/drive.metadata.readonly`

The credential type `gmail` has been removed. Existing stored credentials are migrated
to `google`; local key YAML is not rewritten, so change `type: gmail` to
`type: google`. Keep agent integration/tool/source type `gmail` unchanged.

Dropbox credentials support either OAuth (`source: oauth`, `scopes: [files_metadata]`,
above) or a static credential whose `value` is JSON with all three non-empty fields:

```json
{
  "app_key": "...",
  "app_secret": "...",
  "refresh_token": "..."
}
```

For OAuth, create a Dropbox API app with only `files.metadata.read`, register a
Chief callback URL, and set the app key/secret as the deployment's OAuth application
credentials; users then Authenticate on the Keys page and Chief runs the consent flow.
For a static credential, provision the offline refresh token outside Chief instead.
Either way, choose Full Dropbox access for roots in pre-existing account content.
App Folder access is sufficient only when every configured root is inside that app
folder. For team-space content, configure the appropriate `namespace_id` on the
integration. See [OAuth Application Setup](oauth-apps.md) for the Dropbox app
console steps.

Templates:
[`example-openai.yaml`](../../examples/local/keys/example-openai.yaml),
[`example-google.yaml`](../../examples/local/keys/example-google.yaml), and
[`example-dropbox.yaml`](../../examples/local/keys/example-dropbox.yaml).

---

## Examples

The repository ships reference agent configs under
[`backend/libs/agent_spec/examples/`](../../backend/libs/agent_spec/examples/):

| File | Description |
|------|-------------|
| `minimal.yaml` | Blank starting point — manual trigger, no tools |
| `clock-assistant.yaml` | Manual trigger with the clock tool |
| `gmail-triage.yaml` | Gmail triage with gated tool, inbox source, and queue trigger |
| `cloud-files-browser.yaml` | Metadata-only Google Drive and Dropbox browser with explicit roots |
| `queue-echo.yaml` | Queue processing with test source |
| `clickup-inbox.yaml` | ClickUp INBOX router with gated tool and list source |
| `journal-obsidian.yaml` | Gmail journal messages appended into an Obsidian Sync vault |
| `inbox-triage-usecase.yaml` | Full inbox triage use-case |
| `skills-demo.yaml` | On-demand prompt loading through the automatic `load_skill` tool |

These files demonstrate increasing complexity — from a bare-bones agent to a
full integration with sources, queues, triggers, and allow/deny gating.
