# Obsidian Sync token extraction docs on the Keys page

**Date:** 2026-08-30
**Branch:** `fix/obsidian-token-keys-docs`

## Problem

The `obsidian` credential guide on **Settings → Keys** told the user to "run the Obsidian
Headless (`ob`) login flow … to obtain a Sync auth token" and linked https://obsidian.md/sync.
That is not actionable: the Sync auth token is never displayed in any Obsidian UI, `ob login`
does not print it, and the linked page is the Sync product page rather than the headless CLI
docs. A user adding an `obsidian` credential had no way to learn where the token actually
comes from without digging through forum threads.

## Approach

Rewrite only the `obsidian` entry in `credential_guides.py` — the single source of truth for
the setup steps the Keys page renders — into the concrete CLI recipe: install
`obsidian-headless`, `ob login`, then read the token from the file the login writes
(`~/.config/obsidian-headless/auth_token`, or `~/.obsidian-headless/auth_token` on macOS and
older builds). Also cover the two things that bite afterwards: `ob logout` invalidates the
token, and `ob sync-list-remote` is where the vault id/name for the agent's tool config comes
from. No changes to credential storage, JSON shape, sync behavior, or other credential types.

## Changes

- `backend/apps/keys/credential_guides.py`: replaced the three vague `obsidian` `find_steps`
  with eight concrete steps (prerequisites, install, login, read token file, logout caution,
  vault discovery, E2E password, paste JSON + headless CLI docs link).
- `backend/apps/keys/tests/test_credential_guides.py`: three tests covering the extraction
  recipe, the `ob logout` caution, and vault discovery.
- `backend/apps/web/tests/test_keys_page.py`: test that the recipe reaches the rendered Keys
  page, not just the Python guide table.

## Verification

- Command: `./olib/scripts/orunr py test-all`
- Result: pass — all roots green (`py.lint`, `py.mypy`, `py.bandit`, `py.test`), 1550 backend
  tests, 0 failures. Baseline before the change was green; the four new tests were confirmed
  failing against the old guide first (red step, including a `git stash` re-run for the Keys
  page test).

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Minor | Fixed | `credential_guides.py:115-116` | Fallback path described as “macOS and older CLI builds”; current CLI uses XDG on Linux and `~/.obsidian-headless` on macOS/Windows | Reworded to Linux/XDG vs macOS/Windows; primary path confirmed by operator |
| 2 | Minor | Fixed | `credential_guides.py:117-118` | Official docs only say logout clears stored credentials, not that it revokes the pasted Chief token | Softened to “deletes stored credentials”; still tell the user not to logout before copying |
| 3 | Minor | Fixed | `credential_guides.py:113-114` | 2FA is only prompted when enabled | “and 2FA if enabled” |
| 4 | Minor | Rejected | `test_credential_guides.py:64-69` | Logout test only asserts `ob logout` substring | Still enough to keep the caution in the recipe; the copy is covered by the extraction test’s step join |

## Links

- PR: https://github.com/magicl/chief/pull/52
