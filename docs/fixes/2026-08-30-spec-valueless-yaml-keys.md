# Treat a valueless YAML key as an omitted spec field

**Date:** 2026-08-30
**Branch:** `fix/spec-valueless-yaml-keys`

## Problem

An agent config whose YAML contained a key with no value (`integrations:` on its
own line) failed validation with `integrations / Input should be a valid list
[input_value=None]`. YAML parses such a key as null, and `AgentConfigSpec`
declares `integrations` as `list[IntegrationSpec]` with a `[]` default, so
pydantic rejected the null instead of using the default.

This surfaced as a 500 on `/agents/create/mutate/` because the config editor's
LLM helper revalidates the whole document after applying a mutation, but the same
config would have failed on save and on load by the runner.

The trap was not limited to `integrations`. Empty `limits:`, `triggers:`,
`skills:`, and nested keys such as a tool's `config:` / `allow:` / `deny:` or a
queue's `sources:` had the same failure. Only `tools:` and `queues:` happened to
be normalized already, as a side effect of the integration-resolving validator,
and `integrations` was normalized only for specs stored below version 3, by
migration `003_integrations`.

## Approach

Add one `SpecModel` base class that every spec model inherits. Before field
validation it drops keys whose value is null, unless that field's default is
None — that default is what marks a field as genuinely nullable. This fixes all
the affected fields at once rather than normalizing them one by one, and it
keeps working when fields are added later.

Nullability is read from the field's type annotation rather than from its
default, so explicit nulls that carry meaning are preserved: `credential_ref:
null` on a tool still opts out of an inherited integration credential.

A tool's `allow:` opts out of the normalization through `strict_null_keys`. Its
default is the permissive `['*']`, so silently applying that default to a
malformed permission list would fail open; a valueless `allow:` is rejected
instead.

The config editor's mutation helpers needed the same treatment. They used
`setdefault` and `get(key, [])`, both of which hand back the existing null, so
inserting into or removing from a valueless `tools:` / `triggers:` / `queues:` /
`sources:` still raised and produced a 500.

No schema version bump: nothing about the stored shape changes, only the
leniency of parsing.

## Changes

- `backend/libs/agent_spec/spec.py`: add `SpecModel` with the `_drop_valueless_keys`
  before-validator, the annotation-based `_accepts_null` test, and the cached
  `_keys_accepting_null` lookup; rebase all nine spec models on it; opt `ToolInstance.allow`
  out via `strict_null_keys`.
- `backend/apps/agents/services/config_mutations.py`: read and append through
  `_entries` / `_entries_for_append` so a valueless collection key behaves as empty.
- `backend/apps/agents/tests/test_spec.py`: cover top-level and nested valueless keys,
  the rejected `allow:`, the aliased source `type:`, and surviving explicit nulls.
- `backend/apps/agents/tests/test_config_mutations.py`: regression tests for the reported
  config shape and for each helper acting on a valueless collection.
- `docs/docs/agents.md`: document that an empty optional key means "omitted", and the
  `allow:` exception.

## Verification

- Red: `./olib/scripts/orunr py test` — 3 errors, the new spec tests only, failing with the
  reported `integrations / Input should be a valid list [input_value=None]`.
- Red: `./olib/scripts/orunr py test` with `config_mutations.py` stashed — 6 errors, the new
  mutation tests only.
- Red: `./olib/scripts/orunr py test` with `ToolInstance.strict_null_keys` removed — 1 failure,
  the `allow:` test, confirming it would otherwise resolve to `['*']`.
- Green: `./olib/scripts/orunr py test` — 1569 backend tests OK.
- Green: `./olib/scripts/orunr py test-all` — passed before and after the review fixes.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/apps/agents/services/config_mutations.py:109-159` | Helpers still crashed on a valueless collection: `setdefault` returns the existing null, so `add_tool` called `None.append`, and remove actions iterated null. Same 500 as the reported bug. | Added `_entries` / `_entries_for_append`; six regression tests, verified red with the fix stashed. |
| 2 | Important | Fixed | `backend/libs/agent_spec/spec.py:183-185` | A bare `allow:` resolved to the permissive `['*']` default, turning a malformed permission list into unrestricted tool access. | `strict_null_keys` keeps the null so it fails validation; documented in `docs/docs/agents.md` with a test. |
| 3 | Important | Fixed | `backend/libs/agent_spec/spec.py:21-32` | "Default is None" is only a proxy for nullability; a required nullable field (`x: str \| None` with no default) would have had its valid null dropped. | Nullability now read from the field annotation via `_accepts_null`. |
| 4 | Minor | Fixed | `backend/apps/agents/tests/test_spec.py:152-185` | Top-level test omitted `triggers: None` and there was no validation-alias case. | Added `triggers`/`description` nulls, an aliased source `type:` case, and an explicit-null survival test. |

## Links

- PR: https://github.com/magicl/chief/pull/54
