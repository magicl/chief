# Celery local sync and resource events — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-07-18-celery-local-sync-events-design.md`](./2026-07-18-celery-local-sync-events-design.md)
**Plan:** [`2026-07-18-celery-local-sync-events-plan.md`](./2026-07-18-celery-local-sync-events-plan.md)
**Branch:** `feat/2026-07-18-celery-local-sync-events`
**Review range:** `c20bcd22843c3850c30f4d0a2e703fe0b2b7c64b..fbbeb79380284f39d8c75b6101a3dbb3f530299e` (2026-07-18)

## Assessment

**Ready to merge?** Yes

**Reasoning:** The implementation is production-ready and aligned with the design; all review findings have been resolved.

## Strengths

- Lease ownership, renewal checkpoints, Beat expiry, and atomic release are well designed and tested.
- Committed mutation events are user-scoped, canonical, secret-free, and rollback-safe.
- SSE authentication, isolation, cancellation, Redis outage handling, cleanup, and BFCache lifecycle have strong coverage.
- Htmx partial boundaries preserve unrelated page and form state.
- Watcher removal and application boundaries align with the approved design.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | — | None. | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-design.md:1`; `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-plan.md:1` | The branch adds an unrelated cloud-file integration design and plan despite this feature explicitly excluding that work. Remove those files from this branch so the PR remains independently reviewable. | Removed from the feature diff. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `docs/ARCHITECTURE.md:164` | The library-boundary section still says apps own “boot/watch orchestration,” contradicting the removal of boot synchronization and watchers. Replace it with finite local-provider reconciliation terminology. | Updated to describe `apps.local_sync` finite reconciliation ownership. |

## Recommendations

- Consider an optional Redis-backed integration test for the Lua lease scripts and pub/sub lifecycle; deterministic mocked coverage is already strong.
