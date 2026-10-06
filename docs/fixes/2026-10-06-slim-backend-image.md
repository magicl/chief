# Slim production backend image

**Date:** 2026-10-06
**Branch:** `feat/slim-backend-image`

## Problem

Chief's production backend image was already the slim runtime. Floors and Hello are being aligned to it. The image did not default `DEBUG` and `DJANGO_ENV`, so a container started without the deployment configmap ran the debug entrypoint.

## Approach

**Tests:** code (failing test first)

Set `DEBUG=false` and `DJANGO_ENV=production` on the existing pinned slim image. Apt already installs only `ca-certificates` with `--no-install-recommends` and removes the apt lists. Dev Dockerfiles and the static image stay unchanged.

## Changes

- `backend/Dockerfile.prod`: production defaults for `DEBUG` and `DJANGO_ENV`
- `backend/chief/tests/test_compose_config.py`: locks the shared slim runtime contract

## Verification

- Command: `env -u VIRTUAL_ENV ../.venv/bin/python manage.py test chief.tests.test_compose_config.TestProductionContainerConfig.test_production_backend_image_uses_shared_slim_runtime --testrunner=olib.py.django.test.runner.OTestRunner` from `backend`
- Result: red on missing `ENV DEBUG=false`, then 1 test OK
- Command: `env -u VIRTUAL_ENV -u PYTHONPATH ./olib/scripts/orunr py test-all`
- Result: pass (exit 0). An earlier run failed `py.lint::.` because the shell's `PYTHONPATH` resolved `apps` to infrabase. With `PYTHONPATH` unset, `py.lint::.` passed and the rest of the gate was up to date.

## Review

No findings.

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

## Links

- PR: https://github.com/oivindloe/chief/pull/66
