# Compose nginx naming and shared local pattern

**Branch:** `feat/2026-09-12-compose-nginx-naming`
Status: **design**

Same change lands in **floors**, **hello**, and **chief** (three PRs, same branch name). This file is the chief copy; floors and hello keep an identical spec in their `docs/specs/` trees.

## Problem

Local compose logs for the official `nginx:alpine` image show `/docker-entrypoint.sh` startup, which is expected. Short-lived containers sometimes appear as Docker namesgenerator names (`nervous_banach`) because nothing set `container_name`, and hello’s no-op `Dockerfile.nginx` (`FROM nginx:alpine` plus `build:`) makes the proxy stack diverge from floors/chief and encourages extra recreate/test containers.

Compose project names (`chief-s1-chief-nginx-1`) already exist for the long-lived service. Operators still want a stable, slot-safe name in the log prefix.

## Goals

1. Every local compose nginx container has an explicit, slot-unique `container_name`.
2. Floors, hello, and chief use the same local nginx pattern (image, mounts, watch, naming).
3. Drop hello’s dummy nginx image build. Do **not** add a matching `build:` to floors or chief.

## Non-goals

- Naming backend, nodejs, postgres, or other non-nginx services.
- Kubernetes / production nginx images (`Dockerfile.static` and similar stay as they are).
- Changing proxy routes or `nginx.conf` location blocks.
- Stopping the official image’s `/docker-entrypoint.sh` log lines (those are the image, not the name).

## Chosen approach

Use stock `nginx:alpine` everywhere. Bind-mount config. Set `container_name` from existing `DOCO_SUFFIX` (empty on slot 0, `_1` / `_2` / … on other slots). Restart nginx when the mounted conf file changes. No Dockerfile for the reverse-proxy service.

```yaml
# Reverse proxy — same shape in each app (service/prefix names differ)
<app>-nginx:
  image: nginx:alpine
  container_name: <app>-nginx${DOCO_SUFFIX}
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx-conf.d:/etc/nginx/conf.d:ro
  ports:
    - "${DOCO_PORT:-80}:80"
  develop:
    watch:
      - path: ./nginx.conf
        action: restart
```

```yaml
# Static nginx — floors and chief only (hello has no compose static service today)
<app>-static:
  image: nginx:alpine
  container_name: <app>-static${DOCO_SUFFIX}
  volumes:
    - ../k8s/nginx.static.conf:/etc/nginx/nginx.conf:ro
    # existing static/asset mounts unchanged
  develop:
    watch:
      - path: ../k8s/nginx.static.conf
        action: restart
```

Slot examples: `floors-nginx`, `floors-nginx_1`, `hello-nginx_2`, `chief-static_1`.

`container_name` overrides Compose’s `project-service-N` name. Suffix is required so DOCO slots do not collide.

If the running Compose is older than 2.32 (no `action: restart`), use `sync+restart` with `target: /etc/nginx/nginx.conf` (hello already uses this). Prefer `restart` when available: the bind mount already updates the file.

## Per-repo edits

| Repo | Reverse proxy | Static nginx | Extra |
|------|---------------|--------------|--------|
| floors | `floors-nginx`: add `container_name`, `develop.watch` restart | `floors-static`: same | Ensure `infra/docker/nginx-conf.d/` exists (compose already mounts it; directory is missing in-tree). |
| hello | `hello-nginx`: add `container_name`, empty `nginx-conf.d` mount, `watch` restart; **remove** `build:` / `Dockerfile.nginx` | none in compose | Delete `infra/docker/Dockerfile.nginx`. Keep `Dockerfile.static` (k8s/prod). |
| chief | `chief-nginx`: add `container_name`, `watch` restart | `chief-static`: add `container_name`, `watch` restart | `nginx-conf.d` already present. |

Keep existing `depends_on`, ports, healthchecks, and extra volume mounts (chief static assets path, floors static/loaders).

`DOCO_SUFFIX` is already set in slot overlays for all three apps. Do not add new overlay variables.

## Testing / verification

Compose YAML; no new runtime feature. Verification:

- `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/overlays/slot-0.env config` (and slot-1) interpolates `container_name` as `<app>-nginx` / `<app>-nginx_1`.
- Hello compose config no longer references `Dockerfile.nginx`.
- Optional: `orunr docker compose` on one slot and confirm log prefixes are the new names, not namesgenerator.

No Python/JS test gate change required unless a repo already snapshots compose YAML.

## Rejected alternatives

- **Dummy `Dockerfile.nginx` on floors/chief** — extra build, no image content, more recreate noise.
- **Hard-coded `container_name: floors-nginx`** — breaks parallel DOCO slots.
- **Names only, leave hello’s `build:`** — stacks stay different; one-shot nginx containers likely remain.

## Acceptance

- Local nginx services in floors, hello, and chief compose files share the pattern above.
- Hello has no compose `build` for the reverse proxy.
- Log prefixes for those services are `<app>-nginx${DOCO_SUFFIX}` / `<app>-static${DOCO_SUFFIX}`.
