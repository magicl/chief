
TODO:
- look at agents.md from floors, and carry over what is useful
- create a docs folder with design
- set up asgi server.. same for prod and compose


Design thoughts:
- Read yaml files from github, similar to github actions
- yaml files define ingest, pipes, agents
- Per agent:
--- Tool call to read from and conclude on item in pipe
--- Able to restrict/allow sub commands for tools. I.e. Can allow gmail read/draft, but not send.


## What to build next (2026-06-28)

v0.1 + usability pass is done. Remaining work is closing M6 hardening and moving from
demo agents (hardcoded specs) toward the real product in docs/00-design.md.

### 1. Close out v0.1 (small, high leverage)

- Manual resumability checks: kill worker mid-run → redispatch → confirm RESTART and no
  duplicate work; flush Redis → reload session → confirm DB replay + live tail
- Update README (still says web is placeholder and agents/queues not implemented)
- Validate tools at ingest (ToolPermission.tool against registry in create_agent_from_spec)

### 2. UX polish (docs/TODO.md)

- Markdown + LaTeX rendering for OUTPUT in session UI and run_agent console
- Tool incremental discovery (list/describe/call instead of all functions up front)

### 3. v0.2: Config from files

Plumbing exists: AgentConfigSpec, spec_loader, create_agent_from_spec, run_agent --spec-file.

- Local filesystem source: agent points at path; sync on create/refresh; new AgentConfig +
  immutable Trigger rows when spec changes
- Agent detail UI: config source, revision, dirty flag; Sync now; view current spec
- Replace or supplement hardcoded bootstrap with Import from YAML

Follow-on: GitHub sync (poll/webhook, source_rev, PR-back later).

### 4. Triggers beyond manual

Only manual triggers are wired; Celery beat exists but unused for agents.

- Schedule triggers: beat task for active cron Trigger rows → enqueue sessions
- Agent-to-agent triggers: session A tool/event starts session B

### 5. Pipes / dedup queue (core product bet)

Nothing built yet (deferred in design). Minimal version:

- Pipe + PipeItem models (available / taken / done)
- Builtin tools: pipe.take, pipe.complete
- Fake ingest (management command or beat) to push items
- Agent config declares which pipe it reads from

Sets up email/webhook ingest later.

### 6. Secrets + real tools

Before remote systems (Gmail, webhooks, etc.):

- Connection/secret storage (encrypted credentials per user, referenced from YAML)
- Second real tool (HTTP fetch, email read stub, …) with ToolPermission allow/deny

### 7. Infrastructure / ops (lower urgency)

- ASGI server parity prod vs compose
- Dedicated agent-runs Celery queue
- Lock heartbeat for very long runs (channels.py hook exists, not wired)

### Suggested build order

1. Finish M6 + README
2. YAML local config sync
3. Schedule triggers
4. Pipes + take/complete tool
5. Ingest connectors + secrets

Quick wins path: markdown rendering + README + M6 checks, then YAML.

Product thesis path: minimal pipe with synthetic ingest before GitHub sync.
