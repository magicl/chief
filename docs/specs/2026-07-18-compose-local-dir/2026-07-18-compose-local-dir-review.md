# Docker Compose local directory convention — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-07-18-compose-local-dir-design.md`](./2026-07-18-compose-local-dir-design.md)
**Plan:** [`2026-07-18-compose-local-dir-plan.md`](./2026-07-18-compose-local-dir-plan.md)
**Branch:** `feat/2026-07-18-compose-local-dir`
**Review range:** `d7587c0deb0871cca0bcbcf72fe49a78eca07c76..ade12ce1054e5c7322ba9543bb19c870244351b5` (2026-07-18)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Compose uses one fixed default read-write mount targeting
`/mnt/local` in every consumer, and the regression test rejects conflicting
targets and obsolete local-provider configuration. Focused and full checks pass.

## Strengths

- Compose consistently mounts repository `.local` and fixes
  `CHIEF_LOCAL_DIR=/mnt/local` for all three consumers.
- Legacy user controls are removed and current documentation describes the new
  convention accurately.
- Focused and full Python checks pass.

## Issues

### Critical

None.

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/chief/tests/test_compose_config.py:26-35` | The test filters by source path, so an additional conflicting volume targeting `/mnt/local` would pass. Assert that exactly one volume targets `/mnt/local` and that its source is `../../.local`. | Every volume target is inspected; exactly one must be the default read-write `../../.local:/mnt/local` mount. |

### Minor

None.

## Recommendations

None.
