# Obsidian list/read during first sync — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-08-30-obsidian-read-during-first-sync-design.md`](./2026-08-30-obsidian-read-during-first-sync-design.md)
**Plan:** [`2026-08-30-obsidian-read-during-first-sync-plan.md`](./2026-08-30-obsidian-read-during-first-sync-plan.md)
**Branch:** `feat/2026-08-30-obsidian-read-during-first-sync`
**Review range:** `730ba77e9cb03692f74ca27b80798bd46786d00c..f8e1d998a2a197221877471b323182c04d8bbf64` (2026-08-30; re-reviewed after fixes)

## Assessment

**Ready to merge?** Yes

**Reasoning:** The architecture and behavior matrix are implemented, all review findings are fixed, and the second full-branch review found no remaining Critical, Important, or Minor issues.

## Strengths

- Attempt generations prevent stale first-sync completion after stop/re-ensure.
- Partial reads, ready-only writes, root authorization, and failure precedence are covered across service/API/mock tests.
- The Obsidian tool and HTTP client make one attempt without retry sleep.
- Operator and architecture docs match the new contract.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `services/obsidian/obsidian_vault/supervisor.py:416-425` | A stopped one-shot can return a non-zero exit after its attempt token was invalidated; the stale `HeadlessSyncError` still escapes `ensure_vault` instead of becoming an abandoned-attempt no-op. | Post-wait ownership is checked before exit interpretation; signal-like terminated waits and successor ownership are covered. |
| 2 | Fixed | `services/obsidian/obsidian_vault/app.py:170-172` | Status publishes store `ready` without the short vault lock and `has_references` check, so it can restore readiness during last-reference teardown. | Ensure and status share locked marker recheck/refcount publication; deterministic teardown polling stays gated. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `services/obsidian/obsidian_vault/tests/test_files.py:94-105`, `services/obsidian/obsidian_vault/tests/test_api.py:159-177` | Never-started coverage omits write/append assertions. | Service and HTTP tests now assert write/append remain `sync_pending` before ensure. |
| 2 | Fixed | `backend/libs/clients/obsidian/mock.py:134-152` | The mock cannot represent hard first-sync failure, so it does not model production `unavailable` precedence. | Added resettable failed state with all-op unavailable precedence, unknown-vault precedence, and recovery coverage. |
| 3 | Fixed | `services/obsidian/obsidian_vault/supervisor.py:60` | The enum docstring says lifecycle state serializes in HTTP/JSON even though status intentionally does not expose it. | Docstring now identifies the enum as in-process only and not exposed by status. |

## Recommendations

- Keep attempt ownership as a post-wait invariant as well as a pre-spawn check.
- Keep marker-to-store readiness publication behind the shared lock/refcount rule.
