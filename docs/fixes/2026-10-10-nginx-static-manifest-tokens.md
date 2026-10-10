# Serve web manifests and hide the nginx version

**Date:** 2026-10-10
**Branch:** `fix/nginx-static-manifest-tokens`

## Problem

Stock `mime.types` has no `.webmanifest` entry, so the static nginx server
served `site.webmanifest` as `application/octet-stream`. nginx also advertised
its version in the `Server` header.

## Approach

After `include /etc/nginx/mime.types;` and before `default_type`, add a second
`types` block that maps `webmanifest` to `application/manifest+json` (nginx
merges that block with the included map) and set `server_tokens off` so the
`Server` header omits the version.

## Changes

- `infra/k8s/nginx.static.conf`: manifest MIME mapping and `server_tokens off`.
- `backend/chief/tests/test_nginx_static_conf.py`: assert both settings in the
  span between the mime include and `default_type`.

## Verification

- Red: `env -u PYTHONPATH -u VIRTUAL_ENV ./olib/scripts/orunr py test --fast /workspace/chief/.worktrees/fix-nginx-static-manifest-tokens/backend/chief/tests` failed on `test_webmanifest_maps_to_manifest_json` and `test_server_tokens_off` because those directives were absent between the mime include and `default_type`.
- Green: the same command passed, including both new tests (56 tests, OK).
- Command: `env -u PYTHONPATH -u VIRTUAL_ENV ./olib/scripts/orunr py test-all`
- Result: pass (exit 0). Backend lint, mypy, and tests re-ran. `py.test::backend` ran 1690 tests, OK (skipped=1), including both nginx static conf tests. Inherited `PYTHONPATH` was unset so pylint did not import another checkout's `apps` package. JavaScript gates were not run; this change does not touch JavaScript.

## Review

Code review of this branch against `origin/main` found no Critical, Important, or Minor issues.

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

Status values: `Fixed` | `Rejected` (empty only while review is in progress).

## Links

- PR: pending
