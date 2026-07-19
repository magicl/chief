# Chief dev hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers/subagent-driven-development` to implement this plan task-by-task.
> `/ship` creates the declared feature branch before the first code change. Create
> `docs/specs/2026-07-18-chief-dev-hosting/2026-07-18-chief-dev-hosting-revision.md`
> before implementation and do not read it during implementation. After all
> implementation tasks, return to `/ship` for mandatory S_final review, fixes, and PRs.

**Goal:** Deploy Chief to the private dev Kubernetes cluster as `chief-stage` with
production-safe images, ArgoCD orchestration, Vault-backed secrets, and least-privilege
network policies.

**Architecture:** Chief defines the build and generated environment contract. Infrabase
owns a dedicated Chief Helm chart plus the core-apps ArgoCD composition. The web, worker,
beat, migration, and static workloads share explicit secrets and default-deny networking,
with public HTTPS available only to the worker.

**Tech Stack:** Python 3.13, Django, Celery, uvicorn, Docker, nginx, olib `orun`,
Helm, ArgoCD, External Secrets, Gateway API, Kubernetes NetworkPolicy, Bats

**Branch:** `feat/2026-07-18-chief-dev-hosting`

**ClickUp:** https://app.clickup.com/t/868kdw1ge
**ClickUp branch field:** `feat/2026-07-18-chief-dev-hosting`

The ticket is already `doing`, tagged `agent`, and has the Branch field set. After both
PRs are open and parent verification is green, set it to `review` and comment with both
PR URLs.

---

## Conventions

- Chief commands run from its repository root with `./olib/scripts/orunr …`.
- Chief Python gate: `./olib/scripts/orunr py test-all`.
- Chief JavaScript gates: `./olib/scripts/orunr js test-unit`,
  `./olib/scripts/orunr js lint`, and `./olib/scripts/orunr js tsc`.
- Infrabase chart gates run from its root with `scripts/helm-render-check.sh`,
  `bats scripts/tests/helm_render_check.bats`, and scoped `helm lint` /
  `helm template` commands.
- Use the same `feat/2026-07-18-chief-dev-hosting` branch in Chief and infrabase.
- After each repository stage commit: fetch `origin/main`, rebase on `origin/main`, and
  push. Stop on any rebase conflict.
- Follow TDD where behavior is executable: add a failing test/check, observe the
  expected failure, add the minimal implementation, and rerun green.
- Configuration-only files still require render assertions before they are accepted.
- Every function or method added or materially changed gets a brief purpose docstring or
  leading comment per the repository `AGENTS.md`.
- Python tests use `OTestCase`, `OTransactionTestCase`, or `OLiveServerTestCase`, never
  bare `unittest.TestCase`.
- Capture intentional CLI stdout under test with `self.captureStdout()`.
- Do not add compatibility re-export shims.
- Never mutate a Kubernetes cluster from this plan; all deployment changes are GitOps
  configuration.

## Workspaces

Implementation uses two sibling isolated worktrees:

- Chief: `.worktrees/feat-2026-07-18-chief-dev-hosting`
- infrabase: its own `.worktrees/feat-2026-07-18-chief-dev-hosting`

The Chief worktree contains this design and plan. The infrabase worktree starts from its
own `origin/main`. Commits and PRs remain repository-local even though the branch names
match.

---

### Task 1: Add Chief's stage build and environment contract

**Repository:** Chief

**Files:**
- Modify: `config.py`
- Create: `.env.production.stage`
- Replace: `.env.production`
- Create: `backend/chief/tests/test_deployment_config.py`

- [ ] **Step 1: Write a failing deployment-config test**

Create an `OTestCase` that imports the root `config` module and asserts:

```python
class TestStageDeploymentConfig(OTestCase):
    """Verify the dev-cluster build and environment contract."""

    def test_stage_target_builds_expected_images(self) -> None:
        """Stage uses one backend image for every Python workload."""
        stage = config.Config.clusters['chief-stage']
        self.assertEqual(stage.release_name, 'chief-stage')
        self.assertEqual(stage.cluster, 'dev')
        self.assertEqual(stage.registry_url, 'registry.dev.oivindloe.com')
        self.assertEqual(stage.host, 'https://chief.dev.oivindloe.com')
        self.assertEqual(
            stage.docker_images,
            {
                'backend': './backend/Dockerfile.prod',
                'celery-worker': './backend/Dockerfile.prod',
                'celery-beat': './backend/Dockerfile.prod',
                'static': './infra/k8s/Dockerfile.static.stage',
            },
        )

    def test_stage_environment_uses_production_layers(self) -> None:
        """Generated stage values combine shared production and stage settings."""
        stage = config.Config.envs['chief-stage']
        self.assertEqual(stage.release_name, 'chief-stage')
        self.assertEqual(
            stage.env_files,
            ['.env', '.env.production', '.env.production.stage'],
        )
```

Also assert `default=True`, debug version increments, no pushed version tag, and
`env_repo_path`/app name through `Config.meta.build_context`.

- [ ] **Step 2: Run the scoped test and observe RED**

Run:

```bash
./olib/scripts/orunr py test backend/chief/tests/test_deployment_config.py -v
```

Expected: failure because `chief-stage` and build context do not exist.

- [ ] **Step 3: Add the minimal Chief build target**

Adapt the existing Floors/Hello pattern:

```python
class ClusterInfo(BuildArgoServiceClusterInfo, VersionClusterInfo):
    """Describe one deployable Chief Kubernetes target."""

    host: str
    static_dir: str


@buildArgoService(
    BuildContext(
        app_category='apps',
        app_name='chief',
        env_repo_path=Path('~/yolo/env'),
    )
)
...
class Config:
    clusters = {
        'compose': TargetInfo(...),
        'chief-stage': ClusterInfo(
            release_name='chief-stage',
            cluster='dev',
            docker_images={
                'backend': './backend/Dockerfile.prod',
                'celery-worker': './backend/Dockerfile.prod',
                'celery-beat': './backend/Dockerfile.prod',
                'static': './infra/k8s/Dockerfile.static.stage',
            },
            registry_url='registry.dev.oivindloe.com',
            default=True,
            host='https://chief.dev.oivindloe.com',
            static_dir='backend/.output/static-stage',
            git_must_be_committed_and_pushed=False,
            version_tag_prefix='chief',
            version_increments=['debug'],
            version_push_tag=False,
        ),
    }
```

Preserve the current Compose target and decorators. Configure Postgres's Kubernetes
namespace/service consistently with Floors when required by the CLI.

- [ ] **Step 4: Define non-secret production and stage settings**

`.env.production` contains shared production behavior and file references:

```dotenv
EXECENV_PRODUCTION=true

#[backend]
DEBUG=false
DJANGO_ENV=production
STRUCTURED_LOGGING=true
DJANGO_SECRET_FILE=/etc/secrets/django/secret
CREDENTIALS_KEY_FILE=/etc/secrets/credentials/key
POSTGRES_URL=postgresql://{POSTGRES_USERNAME}:{POSTGRES_PASSWORD}@cnpg-database-cluster-rw.cnpg-database.svc.cluster.local:5432/{POSTGRES_DB}
POSTGRES_USERNAME_FILE=/etc/secrets/postgres/username
POSTGRES_PASSWORD_FILE=/etc/secrets/postgres/password
POSTGRES_DB_FILE=/etc/secrets/postgres/database
REDIS_URL=redis://{REDIS_USERNAME}:{REDIS_PASSWORD}@valkey.valkey.svc.cluster.local:6379
REDIS_USERNAME_FILE=/etc/secrets/redis/username
REDIS_PASSWORD_FILE=/etc/secrets/redis/password
REDIS_PREFIX_FILE=/etc/secrets/redis/prefix
```

`.env.production.stage` contains:

```dotenv
#[backend]
ALLOWED_HOSTS=chief.dev.oivindloe.com,chief-backend.chief-stage.svc.cluster.local
CSRF_TRUSTED_ORIGINS=https://chief.dev.oivindloe.com
LOG_LEVEL=debug
SITE_NAME=Chief
```

Do not set `CHIEF_LOCAL_DIR`.

- [ ] **Step 5: Run GREEN and the scoped config checks**

Run the scoped test again and run:

```bash
./olib/scripts/orunr py lint config.py backend/chief/tests/test_deployment_config.py
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit and synchronize the Chief stage contract**

Commit only Task 1 files with subject `feat: configure Chief stage deployment`, then
fetch/rebase/push the Chief feature branch.

---

### Task 2: Build production-safe Chief runtime images

**Repository:** Chief

**Files:**
- Modify: `.dockerignore`
- Create: `backend/Dockerfile.prod`
- Create: `backend/Dockerfile.prod.dockerignore`
- Create: `infra/k8s/Dockerfile.static.stage`
- Modify: `backend/entrypoint.sh`
- Modify: `backend/chief/tests/test_compose_config.py`
- Modify: `backend/chief/tests/test_deployment_config.py`
- Modify: `config.py`

- [ ] **Step 1: Add failing runtime-contract tests**

Extend the existing container convention tests to assert:

```python
class TestProductionContainerConfig(OTestCase):
    """Verify hosted processes do not perform development bootstrap work."""

    def test_production_web_avoids_dev_bootstrap_and_reload(self) -> None:
        """Production starts only the server; the Argo hook owns migrations."""
        source = (repository_root / 'backend/entrypoint.sh').read_text()
        self.assertIn('if [[ "$DEBUG" == "true" ]]', source)
        self.assertIn('uvicorn chief.asgi:application', source)
        self.assertIn('--reload', source)
        self.assertIn('else', source)
        self.assertIn('--workers', source)

    def test_production_images_run_as_non_root(self) -> None:
        """Backend and static images explicitly select unprivileged users."""
        backend = (repository_root / 'backend/Dockerfile.prod').read_text()
        static = (repository_root / 'infra/k8s/Dockerfile.static.stage').read_text()
        self.assertIn('USER app', backend)
        self.assertIn('USER nginx', static)
```

Use focused assertions or shell invocation tests so the test proves production mode
does not execute `manage.py migrate`, `ensure_superuser`, or `--reload`, while Compose
debug mode retains those operations.

- [ ] **Step 2: Run RED**

```bash
./olib/scripts/orunr py test backend/chief/tests/test_compose_config.py -v
```

Expected: failure because production image files and split startup behavior are absent.

- [ ] **Step 3: Add the production backend image**

Use one digest-pinned `python:3.13-slim` stage, install hash-verified
`chief-backend-env` dependencies from the root workspace lock, remove uv and temporary
export output after installation, copy `olib` and `backend`, create an `app` system
user/group, set `WORKDIR /app`, `USER app`, expose 8000, and invoke
`./entrypoint.sh`. Avoid recommended apt packages, remove apt lists in the same layer,
and do not include development source mounts.

- [ ] **Step 4: Split development and production entrypoint behavior**

Use `DEBUG="${DEBUG:-true}"`. Only debug startup runs migrations and
`ensure_superuser`; only debug uvicorn uses `--reload`. Production web executes uvicorn
with a fixed worker count and no reload. Worker and beat preserve thread-pool and
warning-level behavior. Every invalid `ENTRYPOINT` exits nonzero.

- [ ] **Step 5: Add the static stage image**

Follow the Floors config-driven image pattern. Add a reverse dependency from the
scheduled static image build to the existing `django.collectstatic::backend` config
job. Narrowly re-include `backend/.output/static` in `.dockerignore`, then copy that
generated directory into `/etc/storage/public/static` from a single digest-pinned
nginx image. Reuse `infra/k8s/nginx.static.conf`, create writable `/tmp` paths needed
by nginx, and run as the nginx user with a read-only-root-compatible layout. The
static Dockerfile does not contain Python, uv, application dependencies, or a
`collectstatic` invocation.

- [ ] **Step 6: Run GREEN and build checks**

Run:

```bash
./olib/scripts/orunr py test backend/chief/tests/test_compose_config.py -v
docker build -f backend/Dockerfile.prod -t chief-backend-stage-check .
docker build -f infra/k8s/Dockerfile.static.stage -t chief-static-stage-check .
```

Expected: tests and both builds exit 0.

- [ ] **Step 7: Commit and synchronize the runtime images**

Commit with subject `feat: add production Chief images`, then fetch/rebase/push Chief.

---

### Task 3: Scaffold the dedicated Chief Helm chart

**Repository:** infrabase

**Files:**
- Create: `charts/chief/Chart.yaml`
- Create: `charts/chief/values.yaml`
- Create: `charts/chief/templates/_helpers.tpl`
- Create: `charts/chief/templates/serviceaccount.yaml`
- Create: `charts/chief/templates/configmap.yaml`
- Create: `charts/chief/templates/externalsecrets.yaml`
- Create: `charts/chief/templates/deployment.yaml`
- Create: `charts/chief/templates/service.yaml`
- Create: `charts/chief/templates/httproute.yaml`
- Create: `charts/chief/templates/migration.yaml`
- Create: `charts/chief/README.md`
- Modify: `scripts/helm-render-check.sh`
- Modify: `scripts/tests/helm_render_check.bats`

- [ ] **Step 1: Add a failing Chief chart render contract**

Add `chief` to the simple-chart list and a Bats test that renders with representative
image/config values, parses all YAML documents, and asserts:

- Deployments: `backend`, `celery-worker`, `celery-beat`, `static`;
- Services: `chief-backend`, `chief-static`, both `ClusterIP`;
- one migration Job using the backend image;
- one private Gateway HTTPRoute with `/static` before `/`;
- no `external-dns` annotation;
- ExternalSecrets: `postgres`, `redis`, `django`, `credentials`; and
- no `CHIEF_LOCAL_DIR` value or volume.

The test should call a small Python YAML assertion block from Bats, matching existing
repository style.

- [ ] **Step 2: Run RED**

```bash
bats scripts/tests/helm_render_check.bats
```

Expected: the new Chief-specific test fails because `charts/chief` is absent.

- [ ] **Step 3: Add chart defaults and helpers**

Define image paths:

```yaml
images:
  backend: {image: apps/chief/backend, tag: latest, pullPolicy: IfNotPresent}
  celery-worker: {image: apps/chief/backend, tag: latest, pullPolicy: IfNotPresent}
  celery-beat: {image: apps/chief/backend, tag: latest, pullPolicy: IfNotPresent}
  static: {image: apps/chief/static, tag: latest, pullPolicy: IfNotPresent}
```

Defaults include `registry: OVERRIDE`, `hostname: ""`, `externalDnsHostname: ""`,
one replica per deployment, `restoreMode: false`, explicit resources maps, and:

```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1029
  runAsGroup: 100
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
```

Vault defaults point at `secret/data/apps/chief/{postgres,redis,django,credentials}`.

- [ ] **Step 4: Add secrets, ConfigMap, and workloads**

ExternalSecrets map only required properties:

- Postgres: `username`, `password`, `database`;
- Redis: `username`, `password`, `prefix`;
- Django: `secret`;
- credentials: `key`.

Mount required secrets and `/tmp` into backend, worker, beat, and migration according
to their settings access. All Python deployments use `env-backend`. Backend sets
`ENTRYPOINT=web-server`, worker `celery-worker`, beat `celery-beat`. Static receives no
application secrets or ConfigMap.

Add startup/readiness/liveness probes for backend and static using
`/health/startupz`, `/health/readyz`, and `/health/livez`. During `restoreMode`, scale
runtime Deployments to zero.

- [ ] **Step 5: Add migration, services, and private routing**

Migration is a Sync hook at wave `-1`, uses the backend image and required Postgres,
Django, and credentials mounts, and runs `./manage.py migrate --noinput`. It exits
successfully without migration in restore mode.

The HTTPRoute selects `gateway-private` unless an explicit external hostname is set,
but Chief's core-apps wiring never sets one. Route `/static` to `chief-static:8000` and
the fallback `/` to `chief-backend:8000`, including HSTS response headers.

- [ ] **Step 6: Run GREEN and lint the chart**

```bash
helm lint charts/chief
helm template chief charts/chief -f charts/chief/values.yaml > /tmp/chief-rendered.yaml
bats scripts/tests/helm_render_check.bats
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit and synchronize the chart runtime**

Commit with subject `feat: add Chief deployment chart`, then fetch/rebase/push
infrabase.

---

### Task 4: Enforce Chief's least-privilege network matrix

**Repository:** infrabase

**Files:**
- Create: `charts/chief/templates/network.yaml`
- Modify: `scripts/tests/helm_render_check.bats`

- [ ] **Step 1: Add failing semantic NetworkPolicy assertions**

Render the Chief chart and parse NetworkPolicies by name. Assert:

```text
default-deny-all: ingress=[], egress=[]
backend ingress: envoy-gateway-system -> TCP/8000
backend egress: kube-dns UDP+TCP/53; cnpg-database TCP/5432; valkey TCP/6379
celery-worker ingress: none
celery-worker egress: DNS; CNPG/5432; Valkey/6379; 0.0.0.0/0 TCP/443
celery-beat ingress: none; egress DNS, CNPG/5432, Valkey/6379
migration ingress: none; egress DNS, CNPG/5432
static ingress: envoy-gateway-system -> TCP/8000; egress none
```

For worker public HTTPS, assert exclusions are exactly the intended private/link-local
ranges including `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and
`169.254.0.0/16`. Assert no destination namespace rule omits `ports`.

- [ ] **Step 2: Run RED**

```bash
bats scripts/tests/helm_render_check.bats
```

Expected: the Chief network test fails because policies are absent.

- [ ] **Step 3: Implement one policy per workload**

Create a namespace-wide default deny and policies selected by stable `node` or
`app.kubernetes.io/component` labels. Use separate egress entries for DNS, CNPG,
Valkey, and public HTTPS so namespace selectors cannot grant unintended ports.

Use both UDP and TCP for DNS. Give no ingress stanza to worker, beat, or migration and
no egress stanza to static. Do not copy Floors's broad multi-namespace egress rule.

- [ ] **Step 4: Run GREEN and inspect rendered policies**

```bash
helm template chief charts/chief -f charts/chief/values.yaml |
  kubectl apply --dry-run=client -f -
bats scripts/tests/helm_render_check.bats
```

If local CRD discovery makes `kubectl --dry-run` unavailable, use the semantic Bats
render assertions plus `helm lint`; do not contact or mutate a cluster.

- [ ] **Step 5: Commit and synchronize network hardening**

Commit with subject `feat: restrict Chief workload networking`, then
fetch/rebase/push infrabase.

---

### Task 5: Wire Chief into dev core-apps

**Repository:** infrabase

**Files:**
- Create: `charts/core-apps/templates/chief.yml`
- Modify: `charts/core-apps/values.yaml`
- Modify: `apps/core/core-apps-dev.yaml`
- Modify: `charts/core-apps/README.md`
- Modify: `scripts/tests/helm_render_check.bats`

- [ ] **Step 1: Add failing core-apps topology assertions**

Render core-apps with each deployed environment values file and assert:

- dev contains exactly one `chief-stage` Application;
- kind-test and pub contain no Chief Application;
- the Chief chart source is `charts/chief`;
- generated values come from `$env-repo/chief/chief-stage/values.yml`;
- destination namespace is `chief-stage`;
- `privateHostname` becomes `chief.dev.oivindloe.com`;
- monochart initializes database `chief` from `secret/data/apps/chief/postgres`;
- monochart initializes Valkey prefix `chief` from `secret/data/apps/chief/redis`;
- application sync is automated with prune/self-heal and namespace creation.

- [ ] **Step 2: Run RED**

```bash
bats scripts/tests/helm_render_check.bats
```

Expected: Chief Application/topology assertions fail.

- [ ] **Step 3: Add disabled defaults and dev-only enablement**

Add to `charts/core-apps/values.yaml`:

```yaml
chief:
  enabled: false
  privateHostname: ""
  publicHostname: ""
  releaseName: chief-stage
  resetDb: false
  restoreMode: false
```

Enable only in `apps/core/core-apps-dev.yaml`:

```yaml
chief:
  enabled: true
  privateHostname: chief.dev.oivindloe.com
  releaseName: chief-stage
  resetDb: false
  restoreMode: false
```

Do not add Chief sections to kind-test or pub.

- [ ] **Step 4: Add the multi-source ArgoCD application**

Follow Floors's application shape with three sources:

1. `charts/chief`, reading env-repo values and injecting registry, hostname, restore
   mode, and pod UID/GID;
2. monochart cluster wait plus `clusterDbInit` and `clusterRedisInit`;
3. the `magicl/env` repository as `env-repo`.

Use the dev cluster's existing wait dependencies and explicit sync waves. Keep all
Chief resources in one ArgoCD Application.

- [ ] **Step 5: Document sync waves and secret prerequisites**

Update the concise core-apps README tables to list Chief's application, database/Valkey
initialization, four Vault paths, migration wave, and private route. Do not document
manual cluster mutation.

- [ ] **Step 6: Run GREEN and full infrabase rendering**

```bash
helm lint charts/chief
helm lint charts/core-apps
scripts/helm-render-check.sh
bats scripts/tests/helm_render_check.bats
```

Expected: all commands exit 0 and all deployed environment renders remain valid.

- [ ] **Step 7: Commit and synchronize core-apps wiring**

Commit with subject `feat: deploy Chief to dev`, then fetch/rebase/push infrabase.

---

### Task 6: Integrate and verify both repositories

**Repositories:** Chief and infrabase

**Files:**
- Modify if required: files from Tasks 1–5 only

- [ ] **Step 1: Run Chief's complete Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0 with no failures.

- [ ] **Step 2: Run Chief's configured JavaScript gates**

```bash
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: all exit 0.

- [ ] **Step 3: Rebuild both Chief production images**

```bash
docker build -f backend/Dockerfile.prod -t chief-backend-stage-check .
docker build -f infra/k8s/Dockerfile.static.stage -t chief-static-stage-check .
```

Expected: both exit 0; inspect image configuration to confirm non-root users.

- [ ] **Step 4: Run infrabase's complete chart gates**

```bash
helm lint charts/chief
helm lint charts/core-apps
scripts/helm-render-check.sh
bats scripts/tests/helm_render_check.bats
```

Expected: all exit 0.

- [ ] **Step 5: Verify the acceptance matrix from rendered YAML**

Confirm every design acceptance criterion against tests and rendered manifests:
dev-only Argo app, private route, workload count, image pins, secret mounts, migration
ownership, no default admin/reload/local provider, and exact network matrix. Correct
gaps before proceeding.

- [ ] **Step 6: Commit any verification-driven corrections**

Commit corrections in their owning repository, then fetch/rebase/push each affected
feature branch. Do not combine Chief and infrabase files in one commit.

---

## S_final — Code review (mandatory)

### Task 7: Review both feature branches

> **REQUIRED SKILL:** Return to `/ship` and follow
> `superpowers/requesting-code-review`. Review each repository's complete branch against
> this design and plan. Write the combined findings to
> `docs/specs/2026-07-18-chief-dev-hosting/2026-07-18-chief-dev-hosting-review.md`.
> Under `/ship`, fix every actionable finding, re-verify, and re-review after any
> Critical or Important fix before opening PRs.

**Files:**
- Create: `docs/specs/2026-07-18-chief-dev-hosting/2026-07-18-chief-dev-hosting-review.md`

- [ ] **Step 1: Confirm both repositories' gates pass**

Run Task 6's full Chief and infrabase commands with fresh output.

- [ ] **Step 2: Capture both review ranges**

In each worktree:

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Dispatch the final reviewer**

Provide both repository paths and SHA ranges, this plan, the design, and explicit
attention to secret handling, entrypoint behavior, Argo ordering, HTTPRoute behavior,
and semantic NetworkPolicy restrictions.

- [ ] **Step 4: Write and process the review artifact**

Use one Issues table per severity with `#`, `Status`, `Location`, `Finding`, and
`Notes`. Fix every actionable row and mark it `Fixed`; mark a false finding `Rejected`
only with technical evidence. Re-run review after Critical/Important fixes.

- [ ] **Step 5: Squash, verify, and open both PRs**

After the review is closed, follow `superpowers/finishing-a-development-branch` under
`/ship`: squash each repository branch to one commit, rerun that repository's gates,
push, and create one Chief PR plus one infrabase PR. Keep both worktrees.

- [ ] **Step 6: Complete ClickUp handoff**

After both PRs exist and parent verification is green:

1. set ClickUp task `868kdw1ge` to `review`;
2. leave the `agent` tag in place; and
3. add one comment containing both PR URLs.

---

## Out of scope

- Kubernetes apply/sync/deploy execution.
- Vault secret creation or rotation.
- First-admin creation.
- Public or kind-test Chief deployment.
- Hosted local-provider storage.
- Shared chart refactoring.
