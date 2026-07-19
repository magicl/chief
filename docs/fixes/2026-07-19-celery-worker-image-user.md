# Register the Compose Celery worker identity

**Date:** 2026-07-19
**Branch:** `fix/celery-worker-image-user`

## Problem

Compose ran the Celery worker as numeric uid/gid `1000:1000`, but the development
image had no matching passwd or group entries. Celery treats an unresolved effective
gid as potentially privileged and refused to consume pickle messages.

## Approach

Create a named `app` account in the development image with Compose-provided host ids,
then run the worker as that account. This preserves bind-mount access while allowing
Celery to resolve both the uid and gid without weakening its root-safety check.

## Changes

- `backend/Dockerfile.dev`: create the host-mapped `app` user and group.
- `infra/docker/docker-compose.yml`: pass the identity build arguments for every
  shared dev-image build and run the worker as `app`.
- `backend/chief/tests/test_compose_config.py`: cover the registered worker identity
  contract and continue forbidding `C_FORCE_ROOT`.

## Verification

- Red: `./olib/scripts/orunr py test` failed because the worker still used the
  unresolved numeric identity.
- Green: `./olib/scripts/orunr py test` passed 948 backend tests after the fix.
- Gate: `./olib/scripts/orunr py test-all` passed lint, mypy, tests, and Bandit.
- Gate: `./olib/scripts/orunr js test-unit`, `js lint`, and `js tsc` passed.
- Image: built `backend/Dockerfile.dev` with `APP_UID=1000` and `APP_GID=1000`;
  Python resolved both identities as `app`, and
  `celery.platforms.check_privileges` passed with pickle enabled.
- Collision: built with UID/GID `65534`, which already belong to
  `nobody:nogroup` in the base image; the duplicate `app` identity resolved and
  passed Celery's pickle privilege check.
- Root guard: a build with `APP_UID=0` was rejected before account creation.
- Full Compose startup was not used as evidence because the host's
  `/mnt/infra-assets/chief/js/gen` mount is read-only during the prerequisite asset
  build.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/Dockerfile.dev:32` | Account creation failed when a legitimate host UID/GID already existed in the base image. | Allow non-unique non-root IDs and reject UID/GID 0. |
| 2 | Minor | Fixed | `backend/chief/tests/test_compose_config.py:283` | Coverage did not ensure every service building the shared image used identical identity arguments. | Assert backend, worker, and Beat build arguments. |

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: https://github.com/magicl/chief/pull/28
