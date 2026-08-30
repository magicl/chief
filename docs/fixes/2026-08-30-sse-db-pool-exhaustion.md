# Prevent SSE database pool exhaustion

**Date:** 2026-08-30
**Branch:** `fix/sse-db-pool-exhaustion`

## Problem

Long-lived resource and session SSE responses retained Django database
connections after authentication and replay queries. Four open streams exhausted
psycopg's implicit four-connection pool, making chat and ordinary HTTP requests
wait 30 seconds before failing.

The resource stream also lacked heartbeat frames and its `/events/` route was not
covered by nginx's SSE proxy settings.

## Approach

Explicitly return database connections after the finite database phases of each
stream, before waiting indefinitely on Redis. Give olib's PostgreSQL pool a
bounded default and let Chief override its maximum for the thread-based worker.
Route all SSE endpoints through nginx's unbuffered location and keep the resource
stream alive with the same heartbeat cadence as session streams.

## Changes

- `olib/py/django/app/settingsbase.py`: use a bounded PostgreSQL pool with
  `min_size=1` and `max_size=10`.
- `backend/chief/settings.py`: raise Chief's PostgreSQL pool maximum to 20 for
  the 16-thread agent worker.
- `backend/apps/web/`: release database connections at finite SSE boundaries
  and add resource-stream heartbeats.
- `infra/docker/nginx.conf`: route `/events/` through the unbuffered SSE proxy
  location.
- Tests cover pool configuration, connection-release boundaries, heartbeat
  behavior, and nginx route matching.

## Verification

- Red: `./olib/scripts/orunr py test` — failed on all six new Chief
  regressions before implementation.
- Red: `env -u PYTHONPATH -u VIRTUAL_ENV ./scripts/orunr py test
  olib.py.django.app.tests.test_settingsbase.TestSettingsBaseOwnership.test_postgres_pool_has_bounded_shared_defaults`
  — failed with the former `pool=True` value.
- Green: `./olib/scripts/orunr py test` — passed after implementation and again
  after review fixes.
- Green: `./olib/scripts/orunr py test-all` — passed.
- Green: `env -u PYTHONPATH -u VIRTUAL_ENV ./scripts/orunr py test-all` from
  `olib/` — passed.
- Green: `./olib/scripts/orunr js test-unit` — 6 tests passed.
- Green: `./olib/scripts/orunr js lint` — passed.
- Green: `./olib/scripts/orunr js tsc` — passed.

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|
| 1 | Important | Fixed | `backend/apps/web/resource_events.py` | Resource SSE closed before response middleware but not at generator entry. | Added a second close at the actual long-lived boundary. |
| 2 | Important | Fixed | `backend/apps/web/tests/test_resource_events.py` | Heartbeat coverage inspected source text instead of stream behavior. | Test now consumes idle polls and asserts the emitted heartbeat. |
| 3 | Important | Fixed | `backend/apps/web/tests/test_resource_events.py`, `backend/apps/web/tests/test_sse.py` | Release tests did not cover the resource generator boundary. | Resource test verifies both pre-response and generator-entry closes; session tests cover ownership and replay boundaries. |
| 4 | Minor | Fixed | `docs/fixes/2026-08-30-sse-db-pool-exhaustion.md` | Changes, verification, and review were pending. | Recorded implementation and fresh gate evidence. |
| 5 | Minor | Rejected | `backend/chief/settings.py` | Override assumes the shared pool is a dict and uses Django's canonical PostgreSQL engine string. | The Chief PR pins the matching olib commit atomically; the canonical engine check intentionally avoids configuring non-PostgreSQL backends. |
| 6 | Minor | Fixed | `backend/chief/tests/test_compose_config.py` | Nginx test depended on exact location-line formatting. | Test now extracts the regex, matches `/events/`, and checks buffering and timeout directives. |

## Links

- olib PR: https://github.com/magicl/olib/pull/56
- Chief PR: https://github.com/magicl/chief/pull/53
