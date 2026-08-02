# Obsidian vault service — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are fixed or rejected.

**Design:** [`2026-08-02-obsidian-vault-service-design.md`](./2026-08-02-obsidian-vault-service-design.md)
**Plan:** [`2026-08-02-obsidian-vault-service-plan.md`](./2026-08-02-obsidian-vault-service-plan.md)
**Branch:** `feat/2026-08-02-obsidian-vault-service`
**Review range:** `ee8547db99b249267161c12d0a5ed08a80bc2b2b..e0ccaa73c18de7e8ab034e0bc83634cfe94526b8` (2026-08-02)

## Assessment

**Ready to merge?** Yes — all Critical/Important/Minor findings below are fixed.

**Reasoning:** Strong layering, TDD coverage, and auth-plane documentation. The Compose env leak (Critical #1) is fixed via a dedicated minimal `#[obsidian]` env-split group and a narrowed `ob` subprocess env; ensure/release locking (#2), one-shot timeouts (#3), retry budget (#4), status liveness (#5), pinned dependencies (#6), and fail-fast empty-token handling (#7) are all addressed with accompanying tests, along with the Minor findings (constant-time auth compare, TOCTOU comment, cross-agent-roots test, docs retry-budget note).

## Strengths

- Path gate is segment-aware and well tested
- Clear separation: bindings / files / supervisor / HTTP / Chief client / tool
- FakeSupervisor + injectable Popen make headless supervision unit-testable
- Lifecycle ensure/release never rolls back materialize/delete
- Auth token passed to `ob` via env, not argv

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `infra/docker/docker-compose.yml` (`chief-obsidian` `env_file`); `supervisor.py` `{**os.environ}` | Vault service inherits full backend env (Postgres, CREDENTIALS_KEY, LLM keys, OAuth secrets), violating design acceptance “without Chief DB credentials” and mixing auth planes. Tests currently assert shared env_file. | `chief-obsidian` now uses its own `env.split` group (`#[obsidian]`, declared base-default in `.env.development.compose`, real value from `.env.local`'s `#[backend,obsidian]`) instead of the backend `env_file`/raw `.env.local`. `ObsidianHeadlessSupervisor` now builds a minimal `ob` env (`PATH`/`HOME`/`LANG`/`TZ` + `OBSIDIAN_AUTH_TOKEN`) instead of `{**os.environ}`. `test_compose_config.py` updated/added coverage (`test_vault_service_env_file_excludes_backend_secrets`, `test_vault_service_env_group_always_exists_and_carries_only_the_shared_token`); `test_supervisor.py` adds `test_ensure_vault_passes_minimal_env_not_full_process_environ`. |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 2 | Fixed | `app.py` ensure/release vs `bindings.lock_for` | Supervisor start/stop not under per-vault lock; concurrent ensure can double-start `ob`. | `_start_supervisor_and_maybe_ready` and `release_vaults` now wrap `supervisor.ensure_vault`/`stop_vault` in `store.lock_for(vault_id)`. Covered by `test_api.py::TestVaultApiSupervisorLocking.test_concurrent_ensure_calls_for_same_vault_are_serialized`. |
| 3 | Fixed | `supervisor.py` / `app.py` ensure path | Blocking one-shot `ob sync` with no timeout inside HTTP ensure; can hang FastAPI/materialize. | Added configurable `one_shot_timeout_seconds` (default 120s) to `ObsidianHeadlessSupervisor`; `_run_one_shot` now calls `Popen.wait(timeout=...)`, terminates the child and returns from `ensure_vault` (leaving the vault not-ready, not raising) on `TimeoutExpired`. Covered by new `test_supervisor.py` timeout tests. |
| 4 | Fixed | `libs/tools/tools/obsidian.py` `_DEFAULT_RETRY_DELAYS` | ~3s total retry budget vs plan/docs ~30s stall. | Changed to `(0.5, 1.0, 2.0, 4.0, 8.0, 16.0)`, summing to ~31.5s. `docs/docs/agents.md` updated to state the ~30s budget (also closes Minor #11). |
| 5 | Fixed | `GET /v1/vaults/{vault_id}/status` | Status never reports whether continuous `ob` child is alive. | Added `is_process_alive(vault_id)` to `HeadlessSupervisor`/`FakeSupervisor`/`ObsidianHeadlessSupervisor` (via `Popen.poll()`); status route now returns `sync_process_alive`. Covered by `test_supervisor.py` and `test_api.py::test_status_reports_sync_process_liveness_independent_of_readiness`. |
| 6 | Fixed | `services/obsidian/requirements.txt` / Dockerfile | Unpinned fastapi/uvicorn/httpx and unpinned `obsidian-headless` in image build. | Pinned `requirements.txt` to the versions resolved in `uv.lock` (fastapi==0.141.1, uvicorn[standard]==0.42.0, httpx==0.28.1); Dockerfile pins `obsidian-headless@0.0.14` (latest published at time of writing) via an `OBSIDIAN_HEADLESS_VERSION` build arg-like `ENV`, with a comment on how to re-check before bumping. Covered by new `test_vault_service_pins_python_and_node_dependency_versions`. |
| 7 | Fixed | `main.py` / `auth.py` empty token | Empty `OBSIDIAN_VAULT_TOKEN` silently disables bearer auth. | `main.py` now raises `RuntimeError` at import time if the token is unset/blank. `auth.py`'s `BearerTokenAuth.__call__` also rejects an empty configured token defense-in-depth (never matches, even against a blank header). Covered by new `test_main.py` and `test_api.py::test_empty_configured_token_never_authenticates`. |
| 8 | Fixed | `app.py` `release_vaults` | After `release_agent` returns vault ids at refcount 0, a concurrent ensure can re-bind before `stop_vault`; teardown must re-check refcount under `lock_for`. | `release_vaults` now calls `store.has_references(vault_id)` inside `lock_for` and skips `stop_vault` when another agent re-acquired. Added `VaultBindingStore.has_references`. Covered by `test_bindings.py::test_has_references_reflects_active_bindings` and `test_api.py::TestVaultApiReleaseStopRace.test_release_skips_stop_when_vault_reacquired_before_lock`. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 8 | Fixed | `auth.py` | Token compare not constant-time. | Switched to `hmac.compare_digest` (bundled with the I7 fix above). |
| 9 | Fixed | `files.py` | Path check uses resolve then IO uses unresolved join; Sync TOCTOU note. | Added a comment in `paths.py::resolve_under_roots` explaining the unresolved-join return is intentional (matches what was gate-checked; re-resolving wouldn't close the TOCTOU window against concurrent `ob sync` anyway). |
| 10 | Fixed | bindings/files tests | No two-agent differing-roots cross-access test for acceptance #2. | Added `test_files.py::test_two_agents_sharing_a_vault_cannot_cross_access_each_others_roots`. |
| 11 | Fixed | `docs/docs/agents.md` | “blocks rather than failing immediately” without retry budget. | Folded into the I4 fix: agents.md now states the ~30s retry budget. |
