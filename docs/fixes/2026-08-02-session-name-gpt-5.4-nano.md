# Session name model: gpt-5.4-nano

**Date:** 2026-08-02
**Branch:** `fix/session-name-gpt-5.4-nano`

## Problem

Session auto-naming used OpenAI `gpt-4o-mini`. We want the cheaper/faster
`gpt-5.4-nano` default for chat title generation.

## Approach

Update `ChatNameConfig.model` default (and assert it in unit tests). No other
naming behavior changes.

## Changes

- `backend/libs/algorithms/chat_name.py`: default model `gpt-5.4-nano`
- `backend/libs/algorithms/tests/test_chat_name.py`: assert default model

## Verification

- RED: `./olib/scripts/orunr py test` — `test_default_config_uses_gpt_5_4_nano` failed with `'gpt-4o-mini' != 'gpt-5.4-nano'`
- GREEN / gate: `./olib/scripts/orunr py test-all` — lint, mypy, tests, bandit passed

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No findings. Reviewer assessment: ready to merge.

## Links

- PR: pending
