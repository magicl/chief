# Dropbox OAuth credentials — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-07-19-dropbox-oauth-design.md`](./2026-07-19-dropbox-oauth-design.md)
**Plan:** [`2026-07-19-dropbox-oauth-plan.md`](./2026-07-19-dropbox-oauth-plan.md)
**Branch:** `feat/2026-07-19-dropbox-oauth`
**Review range:** `255cef3816cf4eaf18815a0aa340f89880aa6a4a..5df574e10f3341aeb85a7b0ffa44dc27fdb972b3` (2026-07-19)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Faithful Dropbox plugin on the existing OAuth framework with strong secret-hygiene tests and Google regressions. Shared Dropbox scopes module landed; remaining Minor items rejected as fail-safe or cosmetic.

## Strengths

- Dropbox provider mirrors Google’s secret-scrubbing and grant/envelope contracts.
- Web and disk sync no longer hardcode Google; Google callback regression is tested.
- Client dual-auth is strict (`chief_dropbox_oauth: 1` exact keys) and covered by resolver→client integration tests.
- Docs/Knox/env/callback contracts match the design.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| | | | *(none)* | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/clients/dropbox/client.py`; `backend/apps/keys/oauth/providers/dropbox.py` | Dropbox scope string is duplicated instead of a shared Django-free module like `libs/google_scopes.py`. Drift risk when capabilities grow. | Extracted `libs/dropbox_scopes.py`; provider and client both import from it. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Rejected | `backend/apps/web/views.py` (`settings_keys_oauth_authorize`) | View reads provider for callback URI before `start_authorization` re-fetches; theoretical race could pair stale redirect_uri with fresh provider (fails exchange only). | Fail-safe only; same pattern as Google authorize. Changing would widen the web↔oauth contract for no user-visible benefit. |
| 2 | Rejected | `backend/apps/keys/oauth/providers/dropbox.py` (`exchange_code`) | Scope string `.strip()` check slightly stricter than Google’s; no behavior difference today. | Harmless; Dropbox scopes are fixed identifiers without whitespace. |
| 3 | Fixed | `examples/local/keys/example-dropbox.yaml` | Grammar: “an `source: oauth`” should be “a”. | Fixed in example YAML. `oauth-apps.md` did not contain that phrasing. |

## Recommendations

- Keep future Dropbox capabilities in `libs/dropbox_scopes.py` when the catalog grows.
