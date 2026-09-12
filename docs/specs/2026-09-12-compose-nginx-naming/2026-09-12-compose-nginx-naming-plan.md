# Compose nginx naming — Chief Implementation Plan

> **For agentic workers:** `/impl` first uses `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development`. Create the matching `-revision.md` before implementation and do not read it during implementation. After implementation, run mandatory S_final via `superpowers/requesting-code-review`.

**Goal:** Give Chief reverse-proxy and static nginx containers stable, slot-safe names and a consistent config-restart watch rule.

**Architecture:** Keep stock `nginx:alpine` and existing bind mounts. Use the existing `DOCO_SUFFIX` interpolation for unique names across DOCO slots, and use Compose `action: restart` because bind mounts already update configuration files.

**Tech Stack:** Docker Compose v2+, nginx:alpine, jq-based rendered-config assertions

**Branch:** `feat/2026-09-12-compose-nginx-naming`

---

## Conventions

- Commands run from the repository root.
- Project checks use the `./olib/scripts/orunr …` prefix.
- **Git:** plan docs commit on `main`; implementation uses the branch above. After each implementation commit run `git fetch origin main && git rebase origin/main && git push`.
- This is Compose configuration only; use rendered Compose JSON assertions for red/green verification.
- Preserve all existing ports, dependencies, healthchecks, and static-file mounts.
- Respect `docs/ARCHITECTURE.md`; this infrastructure-only change does not alter application layer boundaries.
- No new functions or compatibility files are introduced.
- **Final task:** code review via `superpowers/requesting-code-review`.

## Task 1: Name and watch Chief nginx services

**Files:**
- Modify: `infra/docker/docker-compose.yml`

- [ ] **Step 1: Verify the desired rendered configuration is absent**

```bash
set -o pipefail
render='docker compose --env-file infra/docker/overlays/slot-0.env -f infra/docker/docker-compose.yml config --no-env-resolution --format json'
test "$($render | jq -r '.services["chief-nginx"].container_name')" = chief-nginx &&
test "$($render | jq -r '.services["chief-static"].container_name')" = chief-static &&
test "$($render | jq -r '.services["chief-nginx"].develop.watch[0].action')" = restart &&
test "$($render | jq -r '.services["chief-static"].develop.watch[0].action')" = restart
```

Expected: non-zero because container names and watch rules are absent.

- [ ] **Step 2: Add slot-safe names and restart watches**

In `infra/docker/docker-compose.yml`, add to `chief-nginx`:

```yaml
container_name: chief-nginx${DOCO_SUFFIX:-}
develop:
  watch:
    - path: ./nginx.conf
      action: restart
```

Add to `chief-static`:

```yaml
container_name: chief-static${DOCO_SUFFIX:-}
develop:
  watch:
    - path: ../k8s/nginx.static.conf
      action: restart
```

Keep the existing tracked `infra/docker/nginx-conf.d/.gitkeep`.

- [ ] **Step 3: Verify slot 0 and slot 1 rendering**

```bash
set -o pipefail
for slot in 0 1; do
  docker compose \
    --env-file "infra/docker/overlays/slot-${slot}.env" \
    -f infra/docker/docker-compose.yml \
    config --no-env-resolution --format json > "/tmp/chief-compose-${slot}.json"
done
test "$(jq -r '.services["chief-nginx"].container_name' /tmp/chief-compose-0.json)" = chief-nginx
test "$(jq -r '.services["chief-static"].container_name' /tmp/chief-compose-0.json)" = chief-static
test "$(jq -r '.services["chief-nginx"].container_name' /tmp/chief-compose-1.json)" = chief-nginx_1
test "$(jq -r '.services["chief-static"].container_name' /tmp/chief-compose-1.json)" = chief-static_1
test "$(jq -r '.services["chief-nginx"].develop.watch[0].action' /tmp/chief-compose-0.json)" = restart
test "$(jq -r '.services["chief-static"].develop.watch[0].action' /tmp/chief-compose-0.json)" = restart
```

Expected: exit 0.

- [ ] **Step 4: Run the repository quality gate**

```bash
./olib/scripts/orunr dev test-all
```

Expected: exit 0.

- [ ] **Step 5: Commit and synchronize**

```bash
git add infra/docker/docker-compose.yml docs/specs/2026-09-12-compose-nginx-naming/
git commit -m "Name local Chief nginx containers"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If the rebase conflicts, stop and ask the human.

---

## S_final — Code review (mandatory)

### Task 2: Code review

> **REQUIRED SKILL:** Read and follow `superpowers/requesting-code-review`. Review the branch against this plan and its design, then write the matching `-review.md`. Under `/ship`, fix all actionable findings before opening the PR.

- [ ] **Step 1: Confirm the quality gate passes**

```bash
./olib/scripts/orunr dev test-all
```

Expected: exit 0.

- [ ] **Step 2: Review the full branch range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

Dispatch the reviewer with both spec paths and the range, then write `docs/specs/2026-09-12-compose-nginx-naming/2026-09-12-compose-nginx-naming-review.md`.

- [ ] **Step 3: Resolve review findings**

Mark every finding `Fixed` or `Rejected` with rationale, rerun verification after fixes, and re-review if any Critical or Important finding was fixed.

- [ ] **Step 4: Finish through a pull request**

Use `superpowers/finishing-a-development-branch` with `/ship`: squash, re-run verification, push, create the PR, and set design status to `review`.

## Out of scope

- Naming non-nginx services.
- Adding custom nginx images or changing proxy routes.
- Changing Kubernetes or production static images.
