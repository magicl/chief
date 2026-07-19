# Google OAuth Credentials — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-07-18-google-oauth-design.md`](./2026-07-18-google-oauth-design.md)
**Plan:** [`2026-07-18-google-oauth-plan.md`](./2026-07-18-google-oauth-plan.md)
**Branch:** `feat/2026-07-18-google-oauth`
**Review range:** `bcaf690d1fabb7e4c9c3ff77de319b19fe3fdbc6..f15db2e3a260d5b69c368e6a7b08a31000d3f971` (2026-07-19)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All actionable findings are fixed, with focused regressions, the uncached full Python gate, migration drift, and Django system checks passing.

## Strengths

- Signed one-time state is bound to session, user, provider, configuration, credential, and grant baseline.
- Disk reconciliation and runtime Google authentication have strong semantic and secret-retention coverage.
- Migration, architecture boundaries, capability allowlisting, service cleanup, and Drive root enforcement are sound.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | — | — | None. | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/middleware.py`; `docs/ARCHITECTURE.md` | Callback query values can enter infrastructure request logs before the view, and exceptional callback responses can bypass route-level cache/referrer hardening. | Route middleware covers converted 404/405/500 responses; tests cover anonymous/success/failure paths, and docs require query-free upstream logs/APM plus proxy isolation. |
| 2 | Fixed | `backend/apps/keys/services/queries.py:66` | OAuth metadata validates only JSON shape, so unknown providers, capabilities, or wrong credential types can become human-facing metadata. | Registry/type validation now requires the exact canonical capability list; query/admin regressions verify safe empty metadata without decryption. |
| 3 | Fixed | `backend/apps/web/views.py:510` | Valid state with neither a code nor a non-empty provider denial consumes state and reports success without replacing a grant. | Missing or empty code now reaches the consuming failure path; denial, code-plus-denial, replay, old-grant retention, and safe surfaces are covered. |
| 4 | Fixed | `backend/chief/settings.py:31` | Callback hardening middleware is installed innermost, so failures converted outside it can bypass the required response headers. | Installed first/outermost; regression covers a downstream response-processing 500 and confirms non-callback responses retain their normal policies. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-plan.md` | Completed implementation and verification checkboxes remain unchecked. | Completed Tasks 1–8 and review-through-fix steps are checked; final commit/PR and ClickUp review-stage work remain pending. |

## Recommendations

- Rerun focused regressions, the full uncached Python gate, migration drift, and Django checks after fixes.
