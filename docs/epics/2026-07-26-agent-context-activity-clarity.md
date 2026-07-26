# Epic: Agent Context and Activity Clarity

Status: **in progress**

Reduce unnecessary provider data sent to LLMs while making agent execution
understandable as a recursive activity tree. Integration projections produce
compact, useful context; typed activity persistence records nested work; and the
session UI presents that work without copying child-session event streams into
their parents.

---

## Specs

- [ ] 1. Compact integration records — [spec](../specs/2026-07-26-compact-integration-records/)
- [ ] 2. Hierarchical session activities — [spec](../specs/2026-07-26-hierarchical-session-activities/)
- [ ] 3. Nested activity UI — [spec](../specs/2026-07-26-nested-activity-ui/)

Build order (implementation): **1 and 2 independently → 3 after 2**

Phasing: Specs 1 and 2 can be implemented in either order or concurrently.
Spec 3 depends on the activity and child-session contracts from spec 2.

---

## Build order

| Step | Spec(s) | Delivers |
|------|---------|----------|
| 1 | 1 | Compact, decoded Gmail and ClickUp context for sources and tools |
| 1 | 2 | Typed nested activities and linked sub-agent sessions |
| 2 | 3 | Recursive compact-tree visualization over activity and child-session streams |

---

## Spec details

### 1. Compact integration records

Project Gmail and ClickUp provider responses into stable, bounded,
LLM-oriented records shared by source adapters and tools. Gmail gains decoded
message content and normalized authentication signals; ClickUp retains task
content, people, planning metadata, relationships, attachments, subtasks, and
recent comments while dropping provider bookkeeping.

### 2. Hierarchical session activities

Replace inferred flat execution history with first-class typed activity trees.
Tool calls become single lifecycle items, LLM calls/spans/status notes may nest
under any activity, and sub-agent nodes point to separately persisted child
sessions. Each child session records its direct `parent_session_id`; top-level
sessions use null.

### 3. Nested activity UI

Render the activity model as a compact recursive tree with collapsed execution
details and live child-session loading. A sub-agent node fetches its child
session by ID rather than embedding or duplicating that session's records in the
parent.

---

## Constraints

- LLM-facing integration records are stable projections, never raw provider
  responses.
- Truncation is deterministic and always represented explicitly.
- Provider clients remain Django-free and retain raw transport responsibilities.
- Any activity may own children.
- A parent session stores only a sub-agent reference node, not the child event
  stream.
- Session ancestry is direct and nullable: roots have no parent; sub-agents
  point to their immediate parent session.
- Recursive visualization must preserve authorization, tolerate arbitrary
  depth, and clean up child stream subscriptions.
- Historical flat tool call/result pairs are migrated into the new activity
  representation.

---

## References

- [Chief architecture](../ARCHITECTURE.md)
- [Gmail integration design](../specs/2026-07-06-gmail-integration/2026-07-06-gmail-integration-design.md)
- [ClickUp integration design](../specs/2026-07-06-clickup-integration/2026-07-06-clickup-integration-design.md)
- [Rich content rendering design](../specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-design.md)
