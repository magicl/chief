# Obsidian vault inter-service token — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-08-29-obsidian-vault-token-design.md`](./2026-08-29-obsidian-vault-token-design.md)
**Plan:** [`2026-08-29-obsidian-vault-token-plan.md`](./2026-08-29-obsidian-vault-token-plan.md)
**Branch:** `feat/2026-08-29-obsidian-vault-token` (chief and infrabase)
**Review range (chief, first pass):** `2a37d47..f3cbbbd` (2026-08-29)
**Review range (infrabase, first pass):** `4e875273..d6c3ebf5` (2026-08-29)

## Assessment

**Ready to merge?** Yes (after review fixes below)

**Reasoning:** Compose hardcode, production `_FILE`, vault-load auto-create, Helm ExternalSecret, and python-only mounts match the design. Migration now blanks `OBSIDIAN_VAULT_TOKEN_FILE` so the Sync hook does not depend on FileAwareEnv's missing-file fallback.

## Strengths

- Single `#[backend,obsidian]` compose token; `.env.local.example` cannot wipe it with a blank assignment
- Hosted path is file-backed only; no git hardcode for stage
- vault-load `create_source_if_missing` mirrors other Chief secrets
- Helm contracts assert python vs static vs migration secret sets

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | — | — | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `charts/chief/templates/migration.yaml` | Migration Job inherits `OBSIDIAN_VAULT_TOKEN_FILE` from `env-backend` without mounting `obsidian`. | Blanked `OBSIDIAN_VAULT_TOKEN_FILE` like Redis; helm-render `expected_migration_env` updated. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/chief/tests/test_compose_config.py` | Architecture token test lived on Google OAuth class. | Moved onto `TestComposeObsidianVaultService`. |
| 2 | Fixed | `backend/chief/tests/test_compose_config.py` | Module-level token constant between classes. | Class attribute `compose_obsidian_vault_token`. |
| 3 | Fixed | `backend/chief/settings.py` | Comment described Compose-only injection. | Mentions hosted `_FILE`. |
| 4 | Fixed | `docs/docs/agents.md` | Knox path not named on the operator page. | Added `$KNOX/chief/{cluster}/obsidian-vault.txt`. |
| 5 | Rejected | Two planned commits vs one per repo | Plan asked for two commits per repo. | One PR-ready commit per reviewable unit is enough. |
| 6 | Fixed | `charts/chief/README.md` | Did not say which workloads mount the token. | Documented python vs static/migration. |
| 7 | Rejected | `scripts/helm-render-check.sh` `require_secret_mounts` | Does not pin `/etc/secrets/obsidian` mountPath. | Existing helper only checks secret names; same as postgres/redis; not unique to this change. |

## Recommendations

- Merge **infrabase first**, then Chief, before the next stage env deploy.
- Do not set `OBSIDIAN_VAULT_URL` in production until a vault workload exists.
