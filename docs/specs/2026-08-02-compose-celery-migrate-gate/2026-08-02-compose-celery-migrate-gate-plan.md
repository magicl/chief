# Compose Celery migrate gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-02-compose-celery-migrate-gate/2026-08-02-compose-celery-migrate-gate-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill). Under `/ship`, return after implementation tasks; `/ship` owns S_final → fix → PR.

**Goal:** Make `chief-beat` and `chief-worker` wait for a healthy `chief-backend` (post-migrate) before starting on Docker Compose.

**Architecture:** Keep migrate ownership in the web-server entrypoint. Gate Celery services with Compose `depends_on` + `condition: service_healthy` on `chief-backend`, whose healthcheck only succeeds after migrate finishes and uvicorn serves `/health/livez`.

**Tech Stack:** Docker Compose YAML, Python/`ruamel.yaml` config regression tests (`OTestCase`)

**Branch:** `feat/2026-08-02-compose-celery-migrate-gate`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped compose test while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-02-compose-celery-migrate-gate`, and after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only — never bare `unittest.TestCase` (`ai/commands/py-checks.md`)
- **Final task:** code review via **`superpowers/requesting-code-review`** (see mandatory **S_final** section below)
- Avoid test names containing parproc-highlighted words (`error`, `warning`, `exception`, `deprecated`)

## File map

- `backend/chief/tests/test_compose_config.py` — add regression coverage that beat and worker require a healthy backend
- `infra/docker/docker-compose.yml` — update `depends_on` for `chief-beat` and `chief-worker`

### Task 1: Gate Celery services on healthy backend

**Files:**
- Modify: `backend/chief/tests/test_compose_config.py`
- Modify: `infra/docker/docker-compose.yml` (`chief-worker` / `chief-beat` `depends_on`)

- [ ] **Step 1: Write the failing test**

Add a new `OTestCase` class (or method on an existing compose test class) in
`backend/chief/tests/test_compose_config.py`. Follow the existing
`TestComposeLocalProviderConfig` pattern: resolve repo root with
`Path(__file__).resolve().parents[3]`, load
`infra/docker/docker-compose.yml` via `YAML(typ='safe')`.

Assert for both `chief-worker` and `chief-beat`:

```python
depends_on = compose['services'][service_name]['depends_on']
self.assertEqual(
    depends_on['chief-backend'],
    {'condition': 'service_healthy'},
)
self.assertEqual(
    depends_on['chief-redis'],
    {'condition': 'service_started'},
)
```

Name the test something like `test_celery_services_wait_for_healthy_backend`
(avoid parproc keywords). Give the class and method brief purpose docstrings.

- [ ] **Step 2: Run test to verify it fails**

```bash
./olib/scripts/orunr py test "$PWD/backend/chief/tests" -k=celery_services_wait_for_healthy_backend
```

Expected: FAIL — `chief-beat` lacks `chief-backend` dependency and/or
`chief-worker` still uses `service_started` for backend.

- [ ] **Step 3: Update Compose dependencies**

In `infra/docker/docker-compose.yml`, set:

```yaml
  chief-worker:
    # ... unchanged fields ...
    depends_on:
      chief-backend:
        condition: service_healthy
      chief-redis:
        condition: service_started

  chief-beat:
    # ... unchanged fields ...
    depends_on:
      chief-backend:
        condition: service_healthy
      chief-redis:
        condition: service_started
```

Do not change entrypoints, healthchecks, or migrate ownership.

- [ ] **Step 4: Run test to verify it passes**

```bash
./olib/scripts/orunr py test "$PWD/backend/chief/tests" -k=celery_services_wait_for_healthy_backend
```

Expected: PASS

- [ ] **Step 5: Full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 6: Commit and sync (PR-ready chunk)**

```bash
git add backend/chief/tests/test_compose_config.py infra/docker/docker-compose.yml
git commit -m "$(cat <<'EOF'
fix(compose): wait for healthy backend before Celery

EOF
)"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase conflicts: stop, do not push, ask the human.

---

## S_final — Code review (mandatory)

### Task 2: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Under `/ship`, the ship skill owns fixing findings and opening the PR — do not stop for a human fix/finish menu.

**Files:** (review only — no edits unless ship asks for fixes)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Read `superpowers/requesting-code-review` skill. Dispatch reviewer subagent with:

- `{DESCRIPTION}` — Compose Celery services wait for healthy backend after migrate
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-08-02-compose-celery-migrate-gate/` design + plan
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Write `docs/specs/2026-08-02-compose-celery-migrate-gate/2026-08-02-compose-celery-migrate-gate-review.md` from `review-file-template.md`. Summarize in chat.

- [ ] **Step 5: Track feedback**

Update **Status** to `Fixed` or `Rejected` as findings are addressed under `/ship`.

- [ ] **Step 6: Human handoff**

Under `/ship`, continue to fix findings and open the PR (do not present the finish menu).

## Out of scope

- Dedicated migrate service
- Entrypoint retry loops
- Changing `livez` / `readyz` semantics
- Kubernetes migration hooks
