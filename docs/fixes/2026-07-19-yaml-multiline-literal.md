# Prefer literal YAML for multiline strings

**Date:** 2026-07-19
**Branch:** `fix/yaml-multiline-literal`

## Problem

Disk-backed agent YAML shown in the config editor serialized multiline strings as quoted scalars with awkward blank continuation lines.

## Approach

Use one shared human-editable YAML dumper for generated agent specs and disk-backed editor text. Multiline strings use literal block scalars while single-line values and parsing semantics remain unchanged.

## Changes

- Added a shared editable-YAML serializer that emits multiline strings as literal blocks.
- Reused the serializer for typed agent specs and disk-backed agent config bodies.
- Preserved the prior 80-column default and quoted strings containing line-break characters that YAML block scalars normalize.
- Added regression coverage for helper-inserted, disk-backed, and direct serializer behavior.

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, tests, bandit).
- Command: `./olib/scripts/orunr js test-unit`
- Result: passed (3 olib tests, 22 web unit tests, 4 browser tests).
- Command: `./olib/scripts/orunr js lint`
- Result: passed.
- Command: `./olib/scripts/orunr js tsc`
- Result: passed.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/libs/file/yaml_dump.py:34` | Shared dumper changed disk-backed YAML width from 80 to 120. | Restored the 80-column default; agent specs still request 120 explicitly. |
| 2 | Important | Fixed | `backend/libs/file/yaml_dump.py:20` | Literal blocks can normalize uncommon line-break characters and alter values. | Such values now use double-quoted serialization and have a round-trip test. |
| 3 | Minor | Fixed | `backend/libs/agent_spec/tests/test_yaml_dump.py:17` | Shared dumper lacked direct formatting and semantic tests. | Added literal, single-line compatibility, and special-line-break coverage. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/26
