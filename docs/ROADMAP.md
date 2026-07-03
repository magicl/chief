

USECASES:
- U1: Clean up my inbox
  - ME: archive old tags I no longer need
  - Goal: Minimize time I spend shuffling emails
    - Tag every email with what action was taken x-action, x-read, x-spam, etc
    - Things I need to do, separately from email -> Create a clickup task for it, in a clickup INBOX
    - Things I need to read, informationally, but not time sensitive, tag #x-read and elevate importance
    - Things I need to act on in the inbox, tag #x-act, and elevate importance more
    - Spam, mark x-spam and move to spam
    - Not important, mark x-unimp (and have gmail organize it)
    - Notes from myself
      - Some could go do my obsidian inbox, with dates for the log entries
      - Some go to clickup, e.g. floors or other projects, but always to an INBOX so we don't mix it up with other things
    - Agent simply looks at the inboxes at a regular schedule. Any email without an x- tag is processed
  - Components
    - Gmail
        - Investigate API vs MCP. I likely want to use a key instead of oauth, so likely API
    - Clickup
        - Investigate API vs MCP
  - Epic: [`docs/epics/2026-07-03-inbox-cleanup.md`](epics/2026-07-03-inbox-cleanup.md) · Format: [`writing-epics`](../../olib/ai/skills/writing-epics/SKILL.md)
  - Specs we need to implement (see epic for checkboxes and build order):
    1. **Key management** — Named, encrypted credentials per user: default LLM keys
       plus arbitrary named keys for external services. UI page to create/edit keys.
       Tools reference a named key at instantiation time (not inline secrets in YAML).
    2. **Agent config schema extensions** — Extend `AgentConfigSpec` beyond today's
       `ToolPermission` (tool name + allow/deny only): support **tool instances**
       (stable id, tool type, named key ref, allow/deny). Required before agent UI
       or inbox agent YAML can express "personal Gmail" vs "work Gmail".
    3. **Sources and queues** — Platform ingest so agents receive **new** items without
       polling/deduping themselves. A **source** (e.g. Gmail inbox poll, webhook,
       RSS) discovers candidates and enqueues **queue items** (deduped by source +
       external id). **Queue tool** for agents: `put`, `take`, `complete`, `fail`.
       One queue item → one agent session (see key considerations). Replaces the
       "pipes" concept in `docs/specs/2026-06-23-design/2026-06-23-design-design.md`.
       U2 reading list will reuse the same primitives.
    4. **Agent configuration UI** — Extends v0.2 file-based config
       (`TODO.md` §3, design doc): YAML/spec sync, revision + dirty flag, import from
       file, and a dashboard page to view/edit the full agent (LLM, prompts, tool
       instances, triggers, queue bindings). Not a replacement for YAML — UI and
       files stay in sync.
    5. **Agent scheduling** — Wire triggers to Celery beat / dispatch. Two kinds
       beyond manual: **schedule** (cron → new session) and **queue** (item available
       on a bound queue → one session per item). Inbox agent uses queue trigger for
       per-email isolation; schedule optional for catch-up or source polling.
    6. **Gmail library and tool** — `libs/gmail` + gated `gmail` tool (list, read,
       label, archive, spam, …; deny send by default). Spec includes API vs MCP vs
       existing library decision; prefer static credentials where the API allows
       (service account / domain delegation — not a simple API key for user mailboxes).
    7. **ClickUp library and tool** — `libs/clickup` + gated tool (create task, list
       lists/spaces, …). API vs MCP decision; personal API token is likely fine.
    8. **Obsidian library and tool** — `libs/obsidian` + tool for appending dated
       notes to an inbox vault path (local REST plugin or file-based — decide in spec).
    9. **Inbox triage agent (U1 product spec)** — The workflow on top of the platform:
       tagging taxonomy (`#x-act`, `#x-read`, `#x-spam`, `#x-unimp`, …), routing rules
       (Gmail label only vs ClickUp INBOX vs Obsidian), system prompt + tool instance
       bindings, source config (Gmail → queue), schedule trigger. Assumes specs 1–8;
       does not re-design them.

    Integration specs (6–8): each documents build-vs-vendor library choice and MCP
    alternative. Agents may attach multiple instances of the same tool type (different
    named keys / accounts).

  - Suggested build order — see epic for full table; summary:
    1–5 platform → 6 Gmail → 7 triage v0 → 8 ClickUp + Obsidian → 9 triage v1

  - Key considerations
    - **One session per email** — queue take starts a dedicated agent session for one
      queue item so context from one message cannot pollute another.
    - **Untagged mail only** — Gmail source adapter enqueues messages lacking `x-*`
      labels; triage agent completes the queue item after applying the chosen action.
    - **ClickUp INBOX** — action items and self-notes routed to ClickUp always land in
      an INBOX list, not mixed into project backlogs.



- U2: Reading list
  - Goal: Find longer form content I should read, related to competitors, technology, management, LLMs, NEWS, etc.
  - Sources:
    - Blogs:
        - Last week in AI
        - Hacker news
        - The labs: OpenAI, Google, etc
  - Format:
    - Initially a list of news articles I can click to each of them
  - Long term
    - Dedupe information. Don't show things I have already seen
    - Do relevance matching, and allow me to provide feedback
    - Automatically discover and add to the list of sources
    - Keep an inventory of articles we have seen and evaluated, and track which I read and which I liked

- U3: Home Assistant automation
  -











USABILITY
- [ ] for both console mode and html mode, we want to render markdown output properly. We also want to support latex formulas if they are present. Make this a clean implementation.
