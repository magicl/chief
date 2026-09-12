# Compose nginx naming and shared local pattern

**Branch:** `feat/2026-09-12-compose-nginx-naming`
Status: **review**

Same change lands in **floors**, **hello**, and **chief** (three PRs, same branch name). This file is the chief copy; the other two apps keep an identical spec in their `docs/specs/` trees.

Hello’s compose static lane is a separate spec ([`2026-09-12-compose-static-assets`](https://github.com/magicl/hello/blob/main/docs/specs/2026-09-12-compose-static-assets/2026-09-12-compose-static-assets-design.md), branch `feat/2026-09-12-compose-static-assets`): `hello-static` (`nginx:alpine`) plus `hello-static-builder`. **This spec does not add that lane.** It names and watches `hello-static` the same way as `floors-static` / `chief-static`. Implement naming **on top of** the static-lane compose file (rebase or merge that branch first if it is not on `main` yet). Do not name `hello-static-builder` here (it is not nginx).

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
- Adding hello’s compose static lane (that is `feat/2026-09-12-compose-static-assets`). This spec only names `hello-static` once it exists.

## Chosen approach

Use stock `nginx:alpine` everywhere. Bind-mount config. Set `container_name` from existing `DOCO_SUFFIX` (empty on slot 0, `_1` / `_2` / … on other slots). Restart nginx when the mounted conf file changes. No Dockerfile for the reverse-proxy service.

```yaml
# Reverse proxy — same shape in each app (service/prefix names differ)
<app>-nginx:
  image: nginx:alpine
  container_name: <app>-nginx${DOCO_SUFFIX:-}
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
# Static nginx — floors, chief, and hello (hello via compose-static-assets)
<app>-static:
  image: nginx:alpine
  container_name: <app>-static${DOCO_SUFFIX:-}
  volumes:
    # keep the conf path this repo already uses
    - <existing nginx.static.conf>:/etc/nginx/nginx.conf:ro
    # existing static/asset mounts unchanged
  develop:
    watch:
      - path: <that same conf file>
        action: restart
```

Hello’s static conf is compose-local (`infra/docker/nginx.static.conf`). Floors and chief keep `infra/k8s/nginx.static.conf`. This spec does not switch hello onto the k8s file.

Slot examples: `floors-nginx`, `floors-nginx_1`, `hello-nginx_2`, `chief-static_1`.

`container_name` overrides Compose’s `project-service-N` name. Suffix is required so DOCO slots do not collide. Write it as `${DOCO_SUFFIX:-}` (explicit empty default) so rendering without a slot env file stays warning-free instead of emitting “variable is not set”.

If the running Compose is older than 2.32 (no `action: restart`), use `sync+restart` with `target: /etc/nginx/nginx.conf` (hello already uses this). Prefer `restart` when available: the bind mount already updates the file.

## Per-repo edits

| Repo | Reverse proxy | Static nginx | Extra |
|------|---------------|--------------|--------|
| floors | `floors-nginx`: add `container_name`, `develop.watch` restart | `floors-static`: same | Ensure `infra/docker/nginx-conf.d/` exists (compose already mounts it; directory is missing in-tree). |
| hello | `hello-nginx`: add `container_name`, empty `nginx-conf.d` mount, `watch` restart; **remove** `build:` / `Dockerfile.nginx` | `hello-static`: add `container_name`; keep existing watch on `./nginx.static.conf` (switch `sync+restart` to `restart` if Compose ≥ 2.32) | Delete `infra/docker/Dockerfile.nginx`. Keep k8s `Dockerfile.static`. Do not recreate `hello-static` / builder — that is `feat/2026-09-12-compose-static-assets`. |
| chief | `chief-nginx`: add `container_name`, `watch` restart | `chief-static`: add `container_name`, `watch` restart | `nginx-conf.d` already present. |

Keep existing `depends_on`, ports, healthchecks, and extra volume mounts (chief static assets path, floors static/loaders).

`DOCO_SUFFIX` is already set in slot overlays for all three apps. Do not add new overlay variables.

## Testing / verification

Compose YAML; no new runtime feature. Verification:

- `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/overlays/slot-0.env config` (and slot-1) interpolates `container_name` as `<app>-nginx` / `<app>-nginx_1`.
- Hello compose config no longer references `Dockerfile.nginx`.
- Hello `hello-static` interpolates `hello-static` / `hello-static_1` (after the static-lane compose file is present).
- Optional: `orunr docker compose` on one slot and confirm log prefixes are the new names, not namesgenerator.

No Python/JS test gate change required unless a repo already snapshots compose YAML.

## Rejected alternatives

- **Dummy `Dockerfile.nginx` on floors/chief** — extra build, no image content, more recreate noise.
- **Hard-coded `container_name: floors-nginx`** — breaks parallel DOCO slots.
- **Names only, leave hello’s `build:`** — stacks stay different; one-shot nginx containers likely remain.

## Acceptance

- Local nginx services in floors, hello, and chief compose files share the pattern above.
- Hello has no compose `build` for the reverse proxy.
- Log prefixes for those services are `<app>-nginx${DOCO_SUFFIX:-}` / `<app>-static${DOCO_SUFFIX:-}`.
