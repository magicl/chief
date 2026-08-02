# Show HTTP status on Provider request failed

**Date:** 2026-08-02
**Branch:** `fix/provider-request-failed-status`

## Problem

When an LLM provider call fails, the dashboard shows `Provider request failed` with no HTTP status, so auth/rate-limit failures (e.g. 401) are hard to diagnose at a glance.

## Approach

Capture an optional `status_code` on `ProviderError` from OpenAI/Anthropic API exceptions, and include it in the user-visible message when present: `Provider request failed (401)`. Leave the message unchanged when no status is available.

## Changes

- `libs/providers/llm/base.py`: add optional `ProviderError.status_code`, `provider_request_failed_message()`, and `status_code_from_exception()`
- `openai_provider.py` / `anthropic_provider.py`: preserve HTTP status on collect failures
- `apps/runner/errors.py`, `loop.py`, `usecases/observability.py`: use the curated message helper so status appears in session failure + LLM activity + JSONL
- Tests for runner, observability, OpenAI, and Anthropic capture paths

## Verification

- Command: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test-all`
- Result: pass (lint, mypy, tests, bandit)

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Fixed | `libs/providers/llm/base.py` | `isinstance(status, int)` accepts `bool` | Aligned with Dropbox guard |
| 2 | Minor | Rejected | `anthropic_provider.py` | Fragmented base imports | isort splits `Usage as ChiefUsage` into its own import; forced three-block layout |
| 3 | Minor | Fixed | `base.py` helpers | No direct unit tests for helpers | Added helper coverage with bool guard |

## Links

- PR: https://github.com/magicl/chief/pull/40
