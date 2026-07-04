# Agent configuration UI — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-design.md` (no `-plan.md` for this spec) — do not read this file unless the user asks.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Spec **4 of 9**
**Design:** [`2026-07-04-agent-config-ui-design.md`](./2026-07-04-agent-config-ui-design.md)
**Branch:** `feat/2026-07-04-agent-config-ui`
**Agent review:** [`2026-07-04-agent-config-ui-review.md`](./2026-07-04-agent-config-ui-review.md) *(separate artifact — do not duplicate here)*

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [x] Function documentation per `AGENTS.md` on spec 4 code (docstrings added)
- [x] DB-only agent config — remove disk bind/sync; examples are the only disk reads

- [x] The create-agent and edit-agent views should be the same.. currently create-agent has no helpers
- [x] In the edit-agent view, the yaml is never populated
- [x] when we create an agent, we should also load a template.. let's have a new template that is a minimal exmample, which we can load when clickng create-agent
- [x] when clicking clock assistnat / queue echo, we currently create an agent, and open the config screen.. this is not the intent.. we shoudl just pre-fill teh create-agent screen with the config, not actually create the agent yet..
- [x] LLM helper.. have a combined dropdown for provider and model, simply called model, listing {provider} - {model}
- [x] Temperature. add a default value
- [x] tool helper. put the tool type in "id" unless there is already another with that name, in htat case do {type}-n whre n is the lowest that works
- [x] tool helper: when a tool is selected, show checkboxes for all the actions it can do. group them in two groups: read first, then write
