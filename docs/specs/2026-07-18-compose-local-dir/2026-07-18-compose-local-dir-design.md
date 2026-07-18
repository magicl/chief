# Docker Compose local directory convention

**Branch:** `feat/2026-07-18-compose-local-dir`
Status: **done**

## Goal

Make Docker Compose load local disk-provider files from the repository's
gitignored `.local/` directory without user-configurable mount-path variables.

## Design

Docker Compose mounts the host repository's `.local/` directory at the fixed
container path `/mnt/local`. Compose sets `CHIEF_LOCAL_DIR=/mnt/local` for the
backend, worker, and beat services, so Chief reads key files from
`.local/keys/*.yaml` and agent files from `.local/agents/*.yaml`.

`CHIEF_AGENTS_DIR` and `CHIEF_KEYS_DIR` are removed because they only customize
container mount targets and provide no application behavior. `CHIEF_LOCAL_DIR`
is removed from `.env.local.example`, but remains an application setting because
non-Compose runtimes and tests use it to select a local-provider root.

## Scope

- Replace the separate keys and agents mounts with one fixed `.local` mount.
- Set the application root explicitly in each Compose service that consumes it.
- Remove the three directory variables from user-facing local environment
  configuration and update current documentation.
- Preserve `CHIEF_LOCAL_WATCH` behavior and non-Compose `CHIEF_LOCAL_DIR`
  support.

## Verification

Add a regression test that parses the Compose configuration and confirms the
fixed mount and `CHIEF_LOCAL_DIR` environment.
Run the required Python checks.

## Acceptance criteria

- No Compose mount references `CHIEF_AGENTS_DIR` or `CHIEF_KEYS_DIR`.
- `.env.local.example` exposes none of the three directory variables.
- All consuming services mount `.local` at `/mnt/local` and receive
  `CHIEF_LOCAL_DIR=/mnt/local`.
- Existing local-provider behavior and required checks pass.
