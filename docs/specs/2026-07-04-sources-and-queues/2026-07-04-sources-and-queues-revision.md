# Sources and queues — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [x] Function documentation per `AGENTS.md` on spec 3 code (docstrings added)
- [x] Remove `apps/agents/spec.py` re-export shim — imports use `libs.agent_spec` only
- [x] `writing-plans` skill: require docstrings + forbid compatibility re-exports in plan Conventions
