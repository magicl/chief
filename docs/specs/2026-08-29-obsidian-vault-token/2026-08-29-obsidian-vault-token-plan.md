# Obsidian vault inter-service token — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/ship` uses `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development` (or `executing-plans` if `inline`) to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-29-obsidian-vault-token/2026-08-29-obsidian-vault-token-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** `/ship` owns **S_final** (`superpowers/requesting-code-review`) — do not run finish menus from the executor.

**Goal:** Hardcode Compose `OBSIDIAN_VAULT_TOKEN` in tracked env, and auto-create a Vault secret plus Helm mount for hosted Python workloads.

**Architecture:** Compose token lives in `.env.development.compose` under `#[backend,obsidian]` so env.split fills both group files. Hosted token is `vault-load` `chief-obsidian-vault` → ExternalSecret `obsidian` → `/etc/secrets/obsidian/token` via `OBSIDIAN_VAULT_TOKEN_FILE`. Two PRs; merge infrabase before (or with) the Chief production `_FILE` deploy.

**Tech Stack:** env.split, Django `FileAwareEnv`, Helm ExternalSecret, `vault-load.py`.

**Branch:** `feat/2026-08-29-obsidian-vault-token` (copy from `-design.md` — must match exactly)

**Infrabase root:** `/workspace/infrabase` (separate git repo; same branch name; second PR).

---

## Conventions

- Commands from **chief** root: `./olib/scripts/orunr …`
- Commands from **infrabase** root: `./olib/scripts/orunr …`
- Gate after Chief stages: `./olib/scripts/orunr py test-all` (or scoped tests while iterating, then full gate)
- Gate after infrabase stages: `./olib/scripts/orunr sh test-all --full` if helm is on PATH; otherwise `bats scripts/tests/helm_render_check.bats -f vault-load` plus `validate_chief_render_contract` via the helm-render-check script when helm exists
- **Git:** plan docs commit on `main`; implementation uses `feat/2026-08-29-obsidian-vault-token`. After each stage commit: `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase`
- **Test names:** do not use the words exception, error, warning, notice, deprecated, deprecation
- **Two PRs:** finish Chief implementation + Chief PR, then infrabase worktree + infrabase PR. Do not mix repos in one commit.
- **Final task:** code review via **`superpowers/requesting-code-review`** (see **S_final**; `/ship` runs it)

---

## File map

### Chief

| File | Responsibility |
|------|----------------|
| `.env.development.compose` | Hardcoded `#[backend,obsidian]` token |
| `.env.local.example` | Optional override docs; no blank assignment |
| `.env.production` | `OBSIDIAN_VAULT_TOKEN_FILE` |
| `docs/ARCHITECTURE.md` | Compose vs Knox mapping |
| `docs/docs/agents.md` | Operator-facing token sources |
| `backend/chief/tests/test_compose_config.py` | Compose env.split contract |
| `backend/chief/tests/test_deployment_config.py` | Production `_FILE` contract |

### infrabase

| File | Responsibility |
|------|----------------|
| `runbooks/vault/vault-load.py` | Auto-create `chief-obsidian-vault` |
| `charts/chief/values.yaml` | `vault.obsidianVaultPath` |
| `charts/chief/templates/externalsecrets.yaml` | ExternalSecret `obsidian` |
| `charts/chief/templates/deployment.yaml` | Mount on python workloads |
| `scripts/helm-render-check.sh` | vault-load + render contracts |
| `charts/chief/README.md`, `charts/core-apps/README.md` | Operator docs |

---

## Stage A — Chief Compose token

### Task 1: Failing compose tests, then hardcoded token

**Files:**

- Modify: `backend/chief/tests/test_compose_config.py` (`TestComposeObsidianVaultService`)
- Modify: `.env.development.compose`
- Modify: `.env.local.example`

- [ ] **Step 1: Write the failing tests**

In `test_vault_service_env_group_always_exists_and_carries_only_the_shared_token`, replace the assertion that the token is empty without `.env.local`. Keep the leak checks. Add an explicit constant for the compose token.

```python
COMPOSE_OBSIDIAN_VAULT_TOKEN = 'compose-obsidian-vault-token'

def test_vault_service_env_group_always_exists_and_carries_only_the_shared_token(self) -> None:
    """Compose bakes the well-known token into both groups without `.env.local`."""
    obsidian_without_local = self._generated_group_values('obsidian', include_local_example=False)
    self.assertEqual(obsidian_without_local.get('OBSIDIAN_VAULT_TOKEN'), COMPOSE_OBSIDIAN_VAULT_TOKEN)

    backend_without_local = self._generated_group_values('backend', include_local_example=False)
    self.assertEqual(backend_without_local.get('OBSIDIAN_VAULT_TOKEN'), COMPOSE_OBSIDIAN_VAULT_TOKEN)

    obsidian_with_local = self._generated_group_values('obsidian', include_local_example=True)
    self.assertEqual(obsidian_with_local.get('OBSIDIAN_VAULT_TOKEN'), COMPOSE_OBSIDIAN_VAULT_TOKEN)

    backend_with_local = self._generated_group_values('backend', include_local_example=True)
    self.assertEqual(backend_with_local['OBSIDIAN_VAULT_TOKEN'], obsidian_with_local['OBSIDIAN_VAULT_TOKEN'])
    for leaked_key in (
        'POSTGRES_URL',
        'POSTGRES_USERNAME',
        'POSTGRES_PASSWORD',
        'REDIS_URL',
        'CREDENTIALS_KEY',
        'OPENAI_API_KEY',
        'ANTHROPIC_API_KEY',
        'GOOGLE_OAUTH_CLIENT_SECRET',
        'DROPBOX_OAUTH_APP_SECRET',
    ):
        self.assertNotIn(leaked_key, obsidian_with_local)
        self.assertIn(leaked_key, backend_with_local)
```

Replace `test_env_example_documents_shared_vault_token` so `.env.local.example` documents an **optional override** and has **zero** `OBSIDIAN_VAULT_TOKEN=` assignment lines:

```python
def test_env_example_does_not_assign_blank_vault_token(self) -> None:
    """`.env.local.example` must not wipe the compose default with a blank assignment."""
    repository_root = Path(__file__).resolve().parents[3]
    env_example = (repository_root / '.env.local.example').read_text()
    self.assertNotRegex(env_example, r'(?m)^OBSIDIAN_VAULT_TOKEN=')
    self.assertIn('OBSIDIAN_VAULT_TOKEN', env_example)
    self.assertIn('#[backend,obsidian]', env_example)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/chief/tests/test_compose_config.py::TestComposeObsidianVaultService -v
```

Expected: FAIL — token still blank / example still assigns `OBSIDIAN_VAULT_TOKEN=`.

- [ ] **Step 3: Minimal env implementation**

`.env.development.compose` — replace the `#[obsidian]` block with:

```text
###############################
#[backend,obsidian]
###############################
# Local-only inter-service bearer (same class as POSTGRES_PASSWORD=nimda).
# Hosted clusters must use Vault (`chief-obsidian-vault`), never this value.
# env.split copies this into both env.compose.backend and env.compose.obsidian.
OBSIDIAN_VAULT_TOKEN=compose-obsidian-vault-token
```

`.env.local.example` — remove the `#[backend,obsidian]` assignment block. Keep a comment:

```text
# Optional override of the compose default token (do not assign a blank value
# here — later files win in env.split). To override locally:
# #[backend,obsidian]
# OBSIDIAN_VAULT_TOKEN=<non-empty>
```

Keep `OPENAI_API_KEY` etc. under `#[backend]` unchanged. `CREDENTIALS_KEY` stays under backend.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/chief/tests/test_compose_config.py::TestComposeObsidianVaultService -v
```

Expected: PASS

- [ ] **Step 5: Commit and sync (PR-ready chunk)**

```bash
git add .env.development.compose .env.local.example backend/chief/tests/test_compose_config.py
git commit -m "$(cat <<'EOF'
fix(compose): hardcode local Obsidian vault inter-service token

EOF
)"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage B — Chief hosted `_FILE` + docs

### Task 2: Production file path and architecture tests

**Files:**

- Modify: `backend/chief/tests/test_deployment_config.py`
- Modify: `backend/chief/tests/test_compose_config.py` (architecture assertions if they live there; otherwise add to `test_deployment_config.py`)
- Modify: `.env.production`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/docs/agents.md`

- [ ] **Step 1: Write the failing tests**

In `test_production_environment_defines_hosted_runtime_contract`, add to the expected dict:

```python
'OBSIDIAN_VAULT_TOKEN_FILE': '/etc/secrets/obsidian/token',
```

Add (in `test_compose_config.py` next to other architecture tests, or `test_deployment_config.py`):

```python
def test_architecture_documents_obsidian_vault_token_sources(self) -> None:
    """Architecture distinguishes compose hardcode from Knox-hosted token."""
    repository_root = Path(__file__).resolve().parents[3]
    architecture = (repository_root / 'docs/ARCHITECTURE.md').read_text()
    self.assertIn('compose-obsidian-vault-token', architecture)
    self.assertIn('`$KNOX/chief/{cluster}/obsidian-vault.txt`', architecture)
    self.assertIn('- `token` → `OBSIDIAN_VAULT_TOKEN`', architecture)
    self.assertIn('OBSIDIAN_VAULT_TOKEN_FILE', architecture)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test backend/chief/tests/test_deployment_config.py::TestStageDeploymentConfig::test_production_environment_defines_hosted_runtime_contract backend/chief/tests/test_compose_config.py -v
```

Expected: FAIL — missing `_FILE` key and architecture strings.

- [ ] **Step 3: Minimal implementation**

`.env.production` under `#[backend]`, after `CREDENTIALS_KEY_FILE`:

```text
OBSIDIAN_VAULT_TOKEN_FILE=/etc/secrets/obsidian/token
```

`docs/ARCHITECTURE.md` — after the existing Obsidian vault paragraph (credentials section), add:

```markdown
**Obsidian inter-service token.** Compose uses the well-known local value
`compose-obsidian-vault-token` from `.env.development.compose`
(`#[backend,obsidian]`). Production has one structured secret:
`$KNOX/chief/{cluster}/obsidian-vault.txt`. Its exact key maps as:

- `token` → `OBSIDIAN_VAULT_TOKEN`

Chief never reads Knox directly. Deployment tooling materializes the token as
`OBSIDIAN_VAULT_TOKEN_FILE=/etc/secrets/obsidian/token` on backend, worker, and
Beat. Do not put the compose hardcode in hosted clusters. `OBSIDIAN_VAULT_URL`
remains unset in production until a vault service workload exists; empty URL
still skips vault ensure.
```

`docs/docs/agents.md` — replace the sentence that says the token comes only from Docker Compose injection with compose **or** hosted `_FILE` / Vault, still never `apps.keys`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test backend/chief/tests/test_deployment_config.py backend/chief/tests/test_compose_config.py -v
```

Expected: PASS

- [ ] **Step 5: Full Chief gate, commit, sync**

```bash
./olib/scripts/orunr py test-all
git add .env.production docs/ARCHITECTURE.md docs/docs/agents.md backend/chief/tests/test_deployment_config.py backend/chief/tests/test_compose_config.py
git commit -m "$(cat <<'EOF'
fix(deploy): read hosted Obsidian vault token from a secret file

EOF
)"
git fetch origin main
git rebase origin/main
git push
```

Expected: `py test-all` exit 0.

---

## Stage C — infrabase vault-load + chart

Work in `/workspace/infrabase` on branch `feat/2026-08-29-obsidian-vault-token` from `origin/main`. Do not commit Chief files here.

### Task 3: Failing vault-load contract, then secret config

**Files:**

- Modify: `scripts/helm-render-check.sh` (`validate_chief_vault_load`)
- Modify: `runbooks/vault/vault-load.py`

- [ ] **Step 1: Extend `expected` in `validate_chief_vault_load`**

Add:

```python
    "chief-obsidian-vault": {
        "vault_path": "secret/apps/chief/obsidian-vault",
        "source_file": "$KNOX/chief/{cluster}/obsidian-vault.txt",
        "format": "env",
        "keys": {"token"},
    },
```

After the credentials Fernet checks, add:

```python
token_field = secrets["chief-obsidian-vault"]["fields"]["token"]
if token_field.get("key") != "token":
    raise SystemExit("chief-obsidian-vault token key must be token")
if token_field.get("length") != 32:
    raise SystemExit("chief-obsidian-vault token length must be 32")
if token_field.get("min_length") != 16:
    raise SystemExit("chief-obsidian-vault token min_length must be 16")
```

- [ ] **Step 2: Run vault-load bats to verify fail**

From infrabase root:

```bash
bats scripts/tests/helm_render_check.bats -f vault-load
```

Expected: FAIL `missing vault-load secret: chief-obsidian-vault`.

- [ ] **Step 3: Add vault-load secret**

In `runbooks/vault/vault-load.py`, immediately after the `chief-credentials` dict, insert:

```python
        {
            'name': 'chief-obsidian-vault',
            'vault_path': 'secret/apps/chief/obsidian-vault',
            'source_file': '$KNOX/chief/{cluster}/obsidian-vault.txt',
            'format': 'env',
            'fields': {
                'token': {'key': 'token', 'length': 32, 'min_length': 16},
            },
            'create_source_if_missing': True,
            'clusters': ['dev'],
        },
```

- [ ] **Step 4: Re-run bats**

```bash
bats scripts/tests/helm_render_check.bats -f vault-load
```

Expected: PASS (2/2 or whatever the vault-load filter count is).

- [ ] **Step 5: Commit and sync**

```bash
git add runbooks/vault/vault-load.py scripts/helm-render-check.sh
git commit -m "$(cat <<'EOF'
feat(vault): auto-create Chief Obsidian inter-service token

EOF
)"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 4: Helm ExternalSecret and mounts

**Files:**

- Modify: `charts/chief/values.yaml`
- Modify: `charts/chief/templates/externalsecrets.yaml`
- Modify: `charts/chief/templates/deployment.yaml`
- Modify: `scripts/helm-render-check.sh` (`app_secrets` and any ExternalSecret name assertions)
- Modify: `charts/chief/README.md`
- Modify: `charts/core-apps/README.md`

- [ ] **Step 1: Fail the render contract first**

In `validate_chief_render_contract`, change:

```python
app_secrets = {"postgres", "redis", "django", "credentials"}
```

to:

```python
app_secrets = {"postgres", "redis", "django", "credentials", "obsidian"}
```

Search the same function for ExternalSecret names / vault paths and add `obsidian` / `secret/data/apps/chief/obsidian-vault` if those sets are asserted. Keep migration mounts `{"postgres", "django", "credentials"}` (no obsidian). Keep static with `set()`.

If helm is available:

```bash
./olib/scripts/orunr sh test scripts/tests/helm_render_check.bats
```

Expected: FAIL python deployments missing `obsidian` secret volume.

If helm is missing, still edit the contract; implement templates next; note helm skip in the revision file.

- [ ] **Step 2: Chart implementation**

`values.yaml` under `vault:`:

```yaml
  obsidianVaultPath: "secret/data/apps/chief/obsidian-vault"
```

`externalsecrets.yaml` list — add:

```yaml
  (dict "name" "obsidian" "path" .Values.vault.obsidianVaultPath "keys" (list "token"))
```

`deployment.yaml` python workloads — add volume + mount next to credentials:

```yaml
            - name: obsidian-secrets-volume
              mountPath: /etc/secrets/obsidian
              readOnly: true
```

```yaml
        - name: obsidian-secrets-volume
          secret:
            secretName: obsidian
```

Do not add these to the static Deployment. Do not add them to `migration.yaml`.

- [ ] **Step 3: Docs**

`charts/chief/README.md` Vault secrets table — add row:

| `secret/data/apps/chief/obsidian-vault` | `$KNOX/chief/{cluster}/obsidian-vault.txt` | `token` |

`charts/core-apps/README.md` — add **Chief Obsidian vault token** (`chief-obsidian-vault`) next to credentials; include it in the “load before enabling” list.

- [ ] **Step 4: Re-run helm/bats**

```bash
bats scripts/tests/helm_render_check.bats -f 'chief'
```

If helm exists, also:

```bash
./olib/scripts/orunr sh test-all --full
```

Expected: exit 0, or document helm-not-on-PATH and passing vault-load + any helm-free checks.

- [ ] **Step 5: Commit and sync**

```bash
git add charts/chief charts/core-apps/README.md scripts/helm-render-check.sh
git commit -m "$(cat <<'EOF'
feat(chief): mount auto-created Obsidian vault token

EOF
)"
git fetch origin main
git rebase origin/main
git push
```

---

## S_final — Code review (mandatory)

### Task 5: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. `/ship` owns this after both PRs' implementation tasks (review each branch's range). Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Write findings to **`docs/specs/2026-08-29-obsidian-vault-token/2026-08-29-obsidian-vault-token-review.md`**. `/ship` fixes actionable findings before opening PRs.

**Files:** (review only — no edits unless fixing findings)

- [ ] **Step 1: Confirm tests pass** (Chief worktree then infrabase)

```bash
./olib/scripts/orunr py test-all
```

- [ ] **Step 2: Get git range** (per repo)

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review** on Chief, then infrabase, against this design and plan.

- [ ] **Step 4: Write review file and report findings**

- [ ] **Step 5: Track feedback** — Status `Fixed` or `Rejected`.

- [ ] **Step 6: Open PRs** — `/ship` finishing skill; infrabase first in the PR description merge-order note; do not merge.

---

## Out of scope

- Kubernetes Obsidian Deployment / Service / NetworkPolicy for `chief-obsidian`
- `OBSIDIAN_VAULT_URL` on stage
- Rotating an already-loaded Knox file if one exists (vault-load creates only when missing)

## References

- Design: `docs/specs/2026-08-29-obsidian-vault-token/2026-08-29-obsidian-vault-token-design.md`
- Prior vault-load: `infrabase/docs/fixes/2026-07-19-chief-vault-load.md`
