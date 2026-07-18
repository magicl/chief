# Docker Compose local directory convention implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers/subagent-driven-development` to implement this plan task-by-task.
> `/impl` creates or checks out the declared feature branch before the first code
> change. Create the matching `-revision.md` before implementation and do not read
> it during implementation. After implementation tasks, return to `/ship` for
> mandatory S_final review, review fixes, and PR creation.

**Goal:** Make Compose load local provider files from fixed `.local/{keys,agents}`
host paths without user-configurable mount paths.

**Architecture:** Compose owns the convention: mount repository `.local` once at
`/mnt/local` and inject `CHIEF_LOCAL_DIR=/mnt/local` into each consuming service.
The Django setting remains configurable for tests and non-Compose use.

**Tech Stack:** Docker Compose YAML, Django/Python configuration tests, Markdown

**Branch:** `feat/2026-07-18-compose-local-dir`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Scoped test while iterating:
  `./olib/scripts/orunr py test "$PWD/backend/chief/tests" -k=compose_uses_fixed_local_provider_directory`
- Final Python gate: `./olib/scripts/orunr py test-all`
- Git: implementation uses the plan branch; after each stage commit run
  `git fetch origin main && git rebase origin/main && git push`
- Every new or materially changed function/method has a brief purpose docstring
  per `AGENTS.md`.
- Test classes use `OTestCase`, never bare `unittest.TestCase`.
- Avoid test names containing parproc-highlighted words such as “error”,
  “warning”, or “deprecated”.
- Do not add compatibility re-export files.

## File map

- `backend/chief/tests/test_compose_config.py`: regression coverage for the fixed
  Compose local-provider mount and environment.
- `infra/docker/docker-compose.yml`: fixed `.local` mount and application root
  for backend, worker, and beat.
- `.env.local.example`: user-editable secrets and watcher toggle only; no mount
  path controls.
- `docs/ARCHITECTURE.md`: canonical Compose local-provider convention.
- `docs/docs/agents.md`: operator-facing local key and agent file locations.

### Task 1: Fix the Compose local-provider convention

**Files:**
- Create: `backend/chief/tests/test_compose_config.py`
- Modify: `infra/docker/docker-compose.yml`
- Modify: `.env.local.example`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/docs/agents.md`

- [x] **Step 1: Write the failing configuration test**

Create a test using `OTestCase`, `pathlib.Path`, and
`ruamel.yaml.YAML(typ='safe')`. Resolve the repository root with
`Path(__file__).resolve().parents[3]`, load `infra/docker/docker-compose.yml`,
and assert for each of `chief-backend`, `chief-worker`, and `chief-beat`:

```python
self.assertIn('../../.local:/mnt/local', service['volumes'])
self.assertEqual(service['environment']['CHIEF_LOCAL_DIR'], '/mnt/local')
```

Also read `.env.local.example` and assert that `CHIEF_LOCAL_DIR`,
`CHIEF_AGENTS_DIR`, and `CHIEF_KEYS_DIR` do not occur. Give the module, test
class, and test method concise purpose documentation.

- [x] **Step 2: Run the test and confirm RED**

Run:

```bash
./olib/scripts/orunr py test "$PWD/backend/chief/tests" -k=compose_uses_fixed_local_provider_directory
```

Expected: FAIL because Compose still has separate parameterized mounts and does
not inject `CHIEF_LOCAL_DIR`.

- [x] **Step 3: Implement the fixed Compose convention**

For each consuming service:

```yaml
environment:
  CHIEF_LOCAL_DIR: /mnt/local
volumes:
  - ../../.local:/mnt/local
```

Preserve existing environment entries such as `ENTRYPOINT`. Remove the separate
`.local/agents` and `.local/keys` mounts and all `CHIEF_AGENTS_DIR` /
`CHIEF_KEYS_DIR` interpolation.

In `.env.local.example`, remove `CHIEF_LOCAL_DIR`, `CHIEF_AGENTS_DIR`, and
`CHIEF_KEYS_DIR` plus comments that instruct users to configure mount targets.
Keep `CHIEF_LOCAL_WATCH`.

Update current documentation to state that Compose reads:

```text
.local/
├── keys/*.yaml
└── agents/*.yaml
```

Clarify that this maps to `/mnt/local` in containers and that
`CHIEF_LOCAL_DIR` remains the generic application setting outside Compose.
Do not rewrite historical spec or epic documents.

- [x] **Step 4: Run the scoped configuration check**

Run:

```bash
./olib/scripts/orunr py test "$PWD/backend/chief/tests" -k=compose_uses_fixed_local_provider_directory
```

Expected: exit 0. The test parses the Compose YAML and confirms each consumer
has `/mnt/local` plus `CHIEF_LOCAL_DIR=/mnt/local`.

- [x] **Step 5: Run the full quality gate**

Run:

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [x] **Step 6: Commit and synchronize the PR-ready chunk**

Commit the implementation, tests, docs, plan checkbox updates, design status,
and revision template as one coherent feature commit. Then run:

```bash
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Stop and report if the rebase conflicts.

---

## S_final — Code review (mandatory)

### Task 2: Full-branch code review and fixes

> **REQUIRED SKILL:** Follow `superpowers/requesting-code-review`. Under `/ship`,
> dispatch the final reviewer, write the matching `-review.md`, fix every
> actionable finding, update every row to `Fixed` or `Rejected` with rationale,
> re-verify, and only then continue to PR creation.

- [x] **Step 1: Confirm the full gate passes**

```bash
./olib/scripts/orunr py test-all
```

- [x] **Step 2: Review the full branch range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
```

Dispatch the reviewer with the design and this plan as requirements.

- [x] **Step 3: Write and resolve the review artifact**

Write
`docs/specs/2026-07-18-compose-local-dir/2026-07-18-compose-local-dir-review.md`.
Fix all valid Critical, Important, and Minor findings and set every issue status
to `Fixed`; use `Rejected` only with a verified technical rationale. Re-run the
full gate after fixes. If a Critical or Important finding was fixed, run one
more full-branch review.

- [x] **Step 4: Create the pull request**

Follow `superpowers/finishing-a-development-branch` in `/ship` mode: squash to
one commit, re-run the full gate, push the feature branch, create the PR, and
set the design status to `review`. Do not merge the PR.

## Out of scope

- Changing Django's generic `CHIEF_LOCAL_DIR` setting or local-provider code.
- Changing watcher behavior.
- Editing historical design, plan, or epic records.
- Supporting configurable Compose host or container paths.
