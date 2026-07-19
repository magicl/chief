# Chief dev hosting — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Statuses are updated as findings are resolved.

**Design:** [`2026-07-18-chief-dev-hosting-design.md`](./2026-07-18-chief-dev-hosting-design.md)
**Plan:** [`2026-07-18-chief-dev-hosting-plan.md`](./2026-07-18-chief-dev-hosting-plan.md)
**Branch:** `feat/2026-07-18-chief-dev-hosting`
**Chief review range:** `bcaf690d1fabb7e4c9c3ff77de319b19fe3fdbc6..90056352e9aa9ec32eddefaf076694a51a7b9b9e` (2026-07-18)
**Infrabase review range:** `82bb84921c35734aa879852d345292abcc2336f4..e2548f5d23fff6fdba709081cfd851f4fdb8ce43` (2026-07-18)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All rollout blockers and hardening findings were fixed and re-reviewed. The implementation matches the approved design and plan with no open findings.

## Strengths

- Clear separation of backend, worker, beat, migration, and static workloads.
- File-backed secrets, private routing, restore suppression, immutable deployment tags, and workload-specific policies are well structured.
- Main workloads satisfy restricted security defaults and avoid service-account token mounting.
- Helm lint and all semantic deployment tests passed before review.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `infrabase:charts/chief/templates/deployment.yaml:56` | Backend probes use the pod IP as HTTP Host, but `KB_POD_IP` is not injected into `ALLOWED_HOSTS`; Django can reject every probe with `DisallowedHost`. | Fixed by Chief `879f473e` and infrabase `3e63125d`; the downward API value and settings behavior are tested. |
| 2 | Fixed | `chief:config.py:161`; `infrabase:charts/core-apps/templates/chief.yml:21` | Chief pushes under `registry.dev.oivindloe.com/apps/chief/*` but pulls through a proxy whose private-registry route covers only `/v2/infra/`, so pulls can fall through to Docker Hub. | Fixed by infrabase `3e63125d`; Chief uses the direct private registry and one backend image reference. |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `infrabase:charts/monochart/templates/clusterdbinit.yml:66`; `clusterredisinit.yml:66` | Shared-namespace initialization jobs receive administrator secrets without narrow NetworkPolicies. | Fixed by `e2548f5d`; selector-specific policies deny ingress and allow only DNS plus the required service port. |
| 2 | Fixed | `infrabase:charts/monochart/templates/clusterwaitfor.yaml:97`; `clusterdbinit.yml:66`; `clusterredisinit.yml:66` | Wait/init hooks lack a complete restricted pod/container security contract and can receive a default ServiceAccount token. | Fixed by `e2548f5d`; hooks are non-root, tokenless, RuntimeDefault, capability-dropped, no-escalation, and read-only. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `infrabase:charts/chief/templates/deployment.yaml:69` | The plan names `/health/startupz`, but Chief has no such endpoint and the chart uses `/health/readyz` for startup. | Fixed by Chief `879f473e` and infrabase `3e63125d`; a tested startup endpoint now backs the startup probe. |
| 2 | Fixed | `chief:backend/Dockerfile.prod:1,16`; `infra/k8s/Dockerfile.static.stage:1,16` | Python, nginx, and uv build images use mutable tags, so rebuilding a commit is not byte-reproducible. | Fixed by `90056352`; Python, uv, and nginx images use tested tag-plus-digest references. |

## Recommendations

- Perform a dev-cluster smoke test after merge for image pulls, migrations, probes, and private routing.
- Keep pinned image digests current through controlled dependency maintenance.

---

## Docker simplification follow-up (2026-07-19)

**Review scope:** Uncommitted simplification changes on
`e451e11ef5a77daba442fe59b47d743b7eea99de`

### Assessment

**Ready to merge?** Yes

**Reasoning:** The config-driven, single-stage images follow the Floors pattern,
retain the requested hardening, and include image-specific context isolation plus
verified temporary-artifact cleanup.

### Strengths

- The config graph correctly schedules `django.collectstatic::backend` before the
  static image.
- The static image cleanly separates asset collection from nginx packaging.
- Digest pins, frozen hash-verified installation, apt cleanup, and non-root runtimes
  remain intact.

### Issues

#### Critical

No findings.

#### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `.dockerignore:7-11`; `backend/Dockerfile.prod:27` | Re-including `backend/.output/static` globally also exposes generated assets to the backend build context and `COPY ./backend /app`, causing unnecessary image content and a possible race with collection. | Added a backend-Dockerfile-specific ignore policy and verified `/app/.output` is absent. |
| 2 | Fixed | `backend/Dockerfile.prod:13-24` | `/deps` and copied workspace manifests remain in the runtime filesystem after installation, contrary to the temporary-artifact cleanup contract. | The install layer now removes `/deps`, the exported requirements, and uv; runtime checks verify they are absent. |

#### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/chief/tests/test_deployment_config.py:60-69`; `backend/chief/tests/test_compose_config.py:341-348` | Source-string tests do not prove the built backend excludes generated static output or temporary installer paths. | Added built-image filesystem checks alongside the source contracts. |

### Recommendations

- Keep the container smoke checks with the source-contract tests when changing image
  construction.

---

## Maintainer follow-up (2026-07-19)

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/Dockerfile.prod` | The backend image should use only `backend/pyproject.toml` and its own lock, without copying root, infra, or olib dependency manifests. | Added a standalone `backend/uv.lock` and matched the Floors backend image pattern. |
| 2 | Fixed | `config.py` | Replace the static-image helper with the standard `cdn.upload` job, depending on backend `collectstatic`. | Added the stage CDN configuration and a backend-static-only upload job before deployment. |
