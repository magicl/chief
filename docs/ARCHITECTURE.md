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
`apps.*`. See `AGENTS.local.md` for the per-app import matrix.

| App | Role |
|-----|------|
| `apps.agents` | Agent models, `AgentConfigSpec`, tool wiring |
| `apps.sessions` | Sessions, event log, session services/tasks |
| `apps.runner` | Celery step loop, LLM + tool invocation |
| `apps.bus` | Redis pub/sub + mailbox |
| `apps.keys` | Encrypted credentials (system + user) |
| `apps.web` | Dashboard, SSE, control endpoints |

Each app exposes **`services/queries.py`** (read) and **`services/commands.py`**
(write). Callers use services, not ad-hoc ORM updates.

---

## Libraries (`libs/`)

| Package | Role |
|---------|------|
| `libs/providers` | LLM provider implementations |
| `libs/tools` | Tool definitions + registry |
| `libs/algorithms` | Reusable algorithms (may call providers) |

Libs stay Django-free. When a lib needs credentials, the **app boundary injects**
callables (`token_supplier`, `secret_supplier`) — libs do not import `apps.keys`.

---

## Credentials & secrets

**Implementation detail:** [`docs/specs/2026-07-03-key-management/`](specs/2026-07-03-key-management/2026-07-03-key-management-design.md)

Architectural rules (all features must follow):

1. **Encrypted store is primary.** Postgres (`apps.keys`); env vars are dev/ops
   fallback for LLM types only when no stored credential exists.
2. **Refs in config, secrets at runtime.** YAML names credentials (`credential_ref`,
   `key_ref`) — never embed values.
3. **Write-only for humans.** UI and admin accept secrets; surfaces show **Set / Not
   set** only — no read-back, hints, or prefilled password fields.
4. **Just-in-time for machines.** Resolve immediately before use; do not retain
   plaintext on session state, config objects, or library client fields.
5. **Type-safe wiring.** Every credential has a `type`; consumers declare
   `expected_type` and reject mismatches.

**Import boundary:** `apps.web` uses metadata queries + commands only (no `resolve_*`).
`apps.runner`, `apps.agents`, and tasks use `resolve_*` / `make_secret_supplier`.
`apps.keys` is a leaf (Django, stdlib, `cryptography` only).
