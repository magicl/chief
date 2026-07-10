# Epic: Inbox cleanup (U1)

Status: **in progress** · Roadmap: [U1](../ROADMAP.md)

Minimize time spent shuffling email: untagged messages are triaged automatically
(tag, route, or dismiss) with **one agent session per message** so context never
mixes across mail.

Methodology: [`writing-epics`](../../olib/ai/skills/writing-epics/SKILL.md) · Each spec links back here from `-design.md` / `-plan.md`.

---

## Specs

- [x] 1. Key management — [spec](../specs/2026-07-03-key-management/)
- [x] 2. Agent config schema extensions — [spec](../specs/2026-07-03-agent-config-schema/) · [plan](../specs/2026-07-03-agent-config-schema/2026-07-03-agent-config-schema-plan.md)
- [x] 3. Sources and queues — [spec](../specs/2026-07-04-sources-and-queues/) · [plan](../specs/2026-07-04-sources-and-queues/2026-07-04-sources-and-queues-plan.md)
- [x] 4. Agent configuration UI — [spec](../specs/2026-07-04-agent-config-ui/)
- [x] 5. Agent scheduling — [spec](../specs/2026-07-05-agent-scheduling/)
- [x] 6. Gmail library and tool — [spec](../specs/2026-07-06-gmail-integration/)
- [x] 7. ClickUp library and tool — [spec](../specs/2026-07-06-clickup-integration/)
- ~~8. Obsidian library and tool~~ — **cancelled** (out of U1; no spec folder)
- [ ] 9. Inbox triage agent
- [ ] 10. Local disk providers (keys + agent configs) — [spec](../specs/2026-07-09-local-disk-providers/)

Build order (implementation): **1 → 2 → 3 → 4 → 5 → 6 → 7 → 10 → 9** (spec 8 cancelled)

Phasing: Gmail + ClickUp integrations are done. Next is **local disk providers
(spec 10)** so oagent/host can edit keys and agent YAML on disk with live reload,
then **inbox triage agent (spec 9)**. Obsidian routing was dropped from U1.

---

## Build order

| Step | Spec(s) | Delivers |
|------|---------|----------|
| 1 | 1 | Encrypted system + user credentials; typed defaults, named keys, env fallback |
| 2 | 2 | Tool instances in YAML (id, type, key ref, allow/deny per instance) |
| 3 | 3 | General sources + robust queues (`max_attempts`, `failed` vs `exhausted`) + queue tool |
| 4 | 4 | YAML-first config UI (everything editable in YAML; UI stays in sync) |
| 5 | 5 | Multiple triggers per agent (cron + queue bindings); idle when done |
| 6 | 6 | Gmail lib + tool + source adapter → queue |
| 7 | 7 | ClickUp lib + tool + source adapter |
| 8 | 10 | Local disk root + providers: load/watch keys and agent configs |
| 9 | 9 | Inbox triage agent (Gmail tag/archive/spam + ClickUp INBOX routing) |
| — | 8 | ~~Obsidian~~ — **cancelled** |

---

## Spec details

### 1. Key management

Encrypted credentials (system + user scopes) as primary store; env fallback for LLM
when absent. Typed defaults per service, named user keys, optional credential ref per
agent/tool instance. Tool instances reference a key by name — no secrets in YAML.

### 2. Agent config schema extensions

**YAML is the source of truth** — ultimately everything about an agent (LLM, prompts,
tool instances, triggers, source/queue bindings) must be expressible in YAML.

Today: `ToolPermission` is tool name + allow/deny only. Add **tool instances**:
stable id, tool type, optional **`credential_ref`**, and **allow/deny on the instance**
(not a separate top-level permissions list). Same field name on the LLM block.
`schema_version` on the spec; v1 configs load via the upgrade chain (in memory) and
persist at the latest version only on explicit save (new config row). Enables multiple
Gmail accounts on one agent. The UI reads/writes the same YAML shape.

### 3. Sources and queues

Platform primitive (replaces “pipes” in the design doc; reused by U2 later).

**Sources (general)**

- A **source** discovers external items (Gmail poll, webhook, RSS, …) and enqueues
  them. Dedupes on `(source, external_id)`.
- Source config is **generic** — adapters are not hardcoded to inbox rules. Each
  source declares **filters** (query, label/tag, date range, etc.). U1 Gmail uses a
  tag/label filter configured to exclude messages already tagged `x-*` (not baked
  into the adapter).

**Queues (robust)**

- Per-queue **`max_attempts`** — max times an item may be taken without reaching
  `done`. Each `take` that does not end in `complete` counts (including stale
  release back to `available`).
- Lifecycle: `available` → `taken` → `done` | `failed` | `exhausted`.
  - **`done`** — taker called `complete`.
  - **`failed`** — taker explicitly called `fail` (agent decided it cannot succeed).
  - **`exhausted`** — taken but not completed `max_attempts` times; terminal, not
    re-queued (distinct from explicit `failed`).
- **`take` is atomic** — two agents/sessions cannot claim the same item; status
  flips to `taken` in one transaction and the item is unavailable to others.
- **Only the taker** (session that took the item) may call `complete` or `fail`.
- **Stale release** — beat/cron reclaims stuck items (increments attempt count when
  returning to `available`; at `max_attempts` → `exhausted` instead):
  - **Minimum hold** — never release before a configured minimum time after `take`
    (even if the session ended early).
  - **Early release** — after minimum hold, release back to `available` only if the
    taking session is **idle** (finished its work) and the item was never completed.
  - **Long hold** — separate, much longer timeout for items still held by a **live**
    (non-idle) session — safety valve without stealing from active work.
- **Queue tool** — gated functions: `put`, `take`, `complete`, `fail`.

One queue item → one agent session (see constraints).

### 4. Agent configuration UI

Extends v0.2 file-based config (`TODO.md` §3): sync from path, revision, dirty
flag, import YAML. Dashboard to view/edit the full agent — but **YAML remains
complete**: every field the runtime needs must be definable without the UI. UI and
YAML stay in sync; not a YAML replacement.

### 5. Agent scheduling

Wire triggers to Celery beat / dispatch. An agent may have **multiple trigger
entries at once** — e.g. one cron schedule plus several queue bindings (`queue1`,
`queue2`, …).

| Kind | Behavior |
|------|----------|
| **schedule** | Cron fires → start a session (e.g. periodic sweep) |
| **queue** | Item available on a bound queue → start one session per item |

When a triggered session **finishes its work**, it returns to **idle** (existing
session state — not a separate “terminated” lifecycle). Idle sessions remain
chat-able; queue release logic keys off idle vs still running.

Inbox agent: **queue** triggers for per-email isolation; **schedule** optional for
catch-up or source polling. Not one long-lived session over many emails.

### 6. Gmail library and tool

`libs/gmail` + gated `gmail` tool: list, read, label, archive, spam (deny send
by default). Spec covers API vs MCP vs vendor lib. Static credentials where Gmail
allows (service account / domain delegation — not a simple API key for user mail).

Gmail **source adapter** (with spec 3) uses configurable filters — U1 configures
a label/tag filter for “not yet tagged `x-*`”, not hardcoded adapter logic.

### 7. ClickUp library and tool

`libs/clickup` + gated tool: create task, list spaces/lists. Personal API token
likely sufficient. API vs MCP decision in spec.

### 8. Obsidian library and tool — **cancelled**

Dropped from U1. No `libs/obsidian` / vault tool in this epic. Self-notes that
would have gone to Obsidian route to **ClickUp INBOX** instead (via spec 7).

### 9. Inbox triage agent

Product spec on top of 1–7 and 10 (spec 8 cancelled). Tagging taxonomy (`#x-act`,
`#x-read`, `#x-spam`, `#x-unimp`, …), routing rules, system prompt, tool instance
bindings. Gmail source uses a **configured filter** (exclude `x-*` labels) →
queue; queue trigger(s). May load keys/agent YAML from the local disk provider
(spec 10) during oagent/host development.

| Outcome | Action |
|---------|--------|
| Act in inbox | `#x-act`, elevate importance |
| Read later | `#x-read`, elevate importance |
| Spam | `#x-spam`, move to spam |
| Unimportant | `#x-unimp`, Gmail organize |
| Todo | ClickUp task → INBOX list |
| Self-note | ClickUp INBOX (Obsidian cancelled) |

Session completes triage → session goes **idle**; agent calls `queue.complete`.
Does not re-design platform specs.

### 10. Local disk providers (keys + agent configs)

**Key providers** (credentials) and **data providers** (agents now; static
resources later) under `libs/providers/{key,data}` (+ LLM under
`libs/providers/llm`); shared hashing in `libs/file`. Django apps own ORM
ingest + boot/watch — **no** `apps.local_disk`. Disk + db now; **GitHub** later
for data. One env root `CHIEF_LOCAL_DIR` with `keys/` + `agents/`. Disk loads at
start and is **watched live**; DB is runtime SoT. Disk-sourced items are
**read-only in the UI**. Required `owner` on key/agent files. Soft-disable when
a watched file disappears.

---

## Constraints

- **One session per email** — queue trigger starts one session per item; no shared context.
- **YAML-complete config** — full agent definition expressible in YAML; UI is optional sugar.
- **U1 Gmail filter** — triage scope via source filter config (`x-*` exclusion), not adapter hardcoding.
- **Atomic queue take** — no double-claim; only taker may complete/fail.
- **Queue failure modes** — explicit `failed` (agent) vs terminal `exhausted` (max attempts).
- **ClickUp INBOX** — routed tasks land in an INBOX list, not project backlogs.
- **DB runtime SoT** — disk/GitHub providers ingest into DB; runners resolve from DB only.
- **Disk UI read-only** — items with `config_source` / key source `disk` are not editable in the UI (v1).

---

## References

- [ROADMAP U1](../ROADMAP.md)
- [Writing epics skill](../../olib/ai/skills/writing-epics/SKILL.md)
- [Chief design](../specs/2026-06-23-design/2026-06-23-design-design.md)
- [v0.2 config from files](../../TODO.md) (§3)
