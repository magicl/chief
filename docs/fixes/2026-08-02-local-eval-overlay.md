# Local eval overlay discovery

**Date:** 2026-08-02
**Branch:** `feat/2026-08-02-local-eval-overlay`

## Problem

Inbox eval scenarios live only under the public `evals/inbox/scenarios/` tree.
Real-email fixtures for personal agents cannot be committed to the public repo,
and `.local/` alone is not versioned.

## Approach

Discover optional private scenarios from `$CHIEF_LOCAL_DIR/evals/inbox/scenarios/`
(fallback: repo `.local/evals/inbox/scenarios/`) alongside the public pack.
Operators symlink a private git checkout (e.g. `chief-private`) to `.local`.
Duplicate scenario ids across public and local packs fail loudly.

## Changes

- `evals/inbox/suite.py`: multi-root scenario discovery + duplicate-id guard
- Docs: agents.md + `.env.local.example` note for private overlay wiring
- Tests: overlay merge and duplicate-id behavior

Companion private-repo PR: `chief-private` example scenario + README.

## Verification

- Command: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test-all`
- Result: pass (lint, mypy, bandit, tests)
- Smoke: `CHIEF_LOCAL_DIR=<chief-private> get_suite().samples()` includes `newsletter-mileage-x-unimp` plus public scenarios

## Review

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| | | | |
