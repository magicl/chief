# Chief Agent Documentation

Chief agents are YAML-configured LLM sessions that perform routine tasks, triggered
manually, on a schedule, or in response to queue items. This document covers the
agent file format, available tools, triggers, queues, integrations, and credentials.

For working examples, see [`backend/libs/agent_spec/examples/`](../../backend/libs/agent_spec/examples/).

---

## Agent file format

An agent is a single YAML file with two layers:

1. **Envelope** — metadata fields that identify the agent on disk.
2. **Config body** — the `AgentConfigSpec` validated by Pydantic.

### Envelope fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `owner` | yes | — | Owner identifier (username or org slug) |
| `identifier` | no | filename stem | Unique agent id within the owner scope |
| `name` | no | same as `identifier` | Human-readable display name |

The envelope is stripped before the config body is validated. All remaining
fields form the `AgentConfigSpec`.

### Config body (`AgentConfigSpec`)

```yaml
schema_version: 3
description: Optional human description
llm:
  provider: openai          # "openai", "anthropic", "local_openai", "repeat"
  model: gpt-5.4-mini
  temperature: 0.7          # optional
  credential_ref: my-key    # optional; falls back to env vars
system_prompt: |
  You are a helpful assistant.
integrations: []   # shared connection configs (see Integrations)
triggers: []       # how the agent is activated (see Triggers)
tools: []          # tool instances available to the LLM (see Tools)
queues: []         # work queues with optional sources (see Queues)
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `3` (literal) | yes | Must match `AGENT_CONFIG_SPEC_VERSION` |
| `description` | string | no | Human-readable purpose of the agent |
| `llm` | `LLMSpec` | yes | LLM provider and model configuration |
| `system_prompt` | string | yes | System prompt injected at session start |
| `integrations` | list of `IntegrationSpec` | no | Shared connection details |
| `triggers` | list of `TriggerSpec` | no | Activation rules |
| `tools` | list of `ToolInstance` | no | Tool instances the LLM can call |
| `queues` | list of `QueueSpec` | no | Agent-scoped work queues |

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
```

### Trigger kinds

| Kind | Description | Required fields |
|------|-------------|-----------------|
| `manual` | User-initiated; no automatic scheduling | — |
| `schedule` | Cron-based periodic execution | `cron`, `prompt` |
| `queue` | Fires when items appear on a named queue | `queue`, `prompt` |
| `agent` | Triggered by another agent's output | `prompt` |

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique trigger name within the agent |
| `kind` | enum | One of `manual`, `schedule`, `queue`, `agent` |
| `cron` | string | Cron expression (required for `schedule`) |
| `queue` | string | Queue id from `queues[]` (required for `queue` kind) |
| `prompt` | string | Injected user-message at session start (required unless `manual`) |
| `max_sessions` | int | Max concurrent sessions; defaults to `1` for schedule/queue, `null` for manual |

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
| `type` | string | — | Tool type (e.g. `clock`, `gmail`, `clickup`, `queue`) |
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

### Built-in tools

#### `clock`

Read-only UTC time. No credentials required.

| Function | Description | Parameters |
|----------|-------------|------------|
| `now` | Return current UTC time as ISO-8601 | — |

#### `gmail`

Gmail operations. Requires a `gmail` credential (OAuth token).

| Function | Description | Readonly |
|----------|-------------|----------|
| `list` | Search messages by Gmail query | yes |
| `read` | Read one message (full body) | yes |
| `list_labels` | List label id/name pairs | yes |
| `get_attachment` | Download an attachment (base64) | yes |
| `label` | Add/remove labels on a message | no |
| `archive` | Remove INBOX label | no |
| `mark_spam` | Move message to spam | no |
| `trash` | Move message to trash | no |
| `send` | Send a message | no |

Typical deny pattern: `deny: [send, trash]` — restrict destructive operations.

#### `clickup`

ClickUp task management. Requires a `clickup` credential (API token).

| Function | Description | Readonly |
|----------|-------------|----------|
| `list_spaces` | List spaces in a workspace | yes |
| `list_lists` | List lists in a space | yes |
| `list_tasks` | List tasks in a list | yes |
| `get_task` | Fetch one task | yes |
| `create_task` | Create a task in a list | no |
| `update_task` | Update task fields | no |
| `create_comment` | Add a comment to a task | no |
| `delete_task` | Delete a task | no |

Typical deny pattern: `deny: [delete_task]`.

Config key: `team_id` (workspace id for `list_spaces` default).

#### `queue`

Agent-scoped queue operations. No external credential — bound to the session's
agent and session ids at runtime.

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
| `config` | object | Adapter-specific settings (query, list_id, etc.) |

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
| `config` | object | Shared non-secret configuration |

When a tool or source sets `integration: <id>`:
- `type` is inherited (must match if explicitly set on the tool/source).
- `credential_ref` is inherited unless the tool/source overrides or explicitly nulls it.
- `config` is merged (tool/source config wins on key conflicts).

---

## Credentials

Credentials supply secrets (API keys, OAuth tokens) to LLM providers, tools,
and sources without embedding them in agent YAML.

### Credential references

| Context | Field | Description |
|---------|-------|-------------|
| LLM | `llm.credential_ref` | Provider API key; falls back to env vars if omitted |
| Tool | `tools[].credential_ref` | Per-tool credential; inherits from integration |
| Source | `sources[].credential_ref` | Per-source credential; inherits from integration |
| Integration | `integrations[].credential_ref` | Shared credential for all referencing tools/sources |

### Key file format

Key files live in `$CHIEF_LOCAL_DIR/keys/` (default: `~/.chief/keys/`).
Each is a YAML file:

```yaml
name: my-openai-key
type: openai
owner: your-username
value: sk-...
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Key name (matches `credential_ref` values) |
| `type` | string | Credential type (`openai`, `gmail`, `clickup`, etc.) |
| `owner` | string | Owner scope |
| `value` | string | The secret value |

See [`examples/local/keys/example-openai.yaml`](../../examples/local/keys/example-openai.yaml) for a template.

---

## Examples

The repository ships reference agent configs under
[`backend/libs/agent_spec/examples/`](../../backend/libs/agent_spec/examples/):

| File | Description |
|------|-------------|
| `minimal.yaml` | Blank starting point — manual trigger, no tools |
| `clock-assistant.yaml` | Manual trigger with the clock tool |
| `gmail-triage.yaml` | Gmail triage with gated tool, inbox source, and queue trigger |
| `queue-echo.yaml` | Queue processing with test source |
| `clickup-inbox.yaml` | ClickUp INBOX router with gated tool and list source |
| `inbox-triage-usecase.yaml` | Full inbox triage use-case |

These files demonstrate increasing complexity — from a bare-bones agent to a
full integration with sources, queues, triggers, and allow/deny gating.
