# Own the credentials setting in Chief

**Date:** 2026-07-18
**Branch:** `fix/credentials-setting-scope`

## Problem

Chief's credential-encryption key policy was duplicated in olib's shared Django
settings, causing unrelated production consumers to require `CREDENTIALS_KEY`.

## Approach

Keep Chief's existing `env_secret` declaration and fixed development Fernet key,
update the olib submodule after removing the shared declaration, and add a
regression test proving production validation comes from `env_secret`.

## Changes

- Chief settings: retain the Chief-owned `env_secret` declaration and fixed
  development default.
- olib submodule: update to the shared-settings ownership fix.
- Regression coverage: verify production reaches `env_secret`'s required-secret
  validation after the shared declaration is removed.

## Verification

- Baseline: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test-all`
- Result: backend checks passed; the old pinned olib revision had one launcher
  self-test failure that is revised on current olib.
- Red test: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test`
- Result: Chief did not yet expose `env_secret`'s production validation because
  the old shared setting failed first.
- Green tests: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test`
- Result: passed after updating olib.
- Full gate: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test-all`
- Result: passed (lint, mypy, tests, and Bandit).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No findings.

## Links

- olib PR: pending
- PR: pending
