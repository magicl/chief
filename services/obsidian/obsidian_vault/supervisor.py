# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Headless Obsidian Sync (`ob`) process supervision, one process set per vault.

`HeadlessSupervisor` is the interface the rest of the vault service (bindings,
HTTP layer) depends on for vault lifecycle: start/reuse sync for a vault,
tear it down, and check whether its initial full sync has completed.
`FakeSupervisor` is an in-memory stand-in for unit and future API tests.
`ObsidianHeadlessSupervisor` is the real implementation, shelling out to the
official `obsidian-headless` (`ob`) CLI via injectable subprocess factories
so it can be unit-tested without a real `ob` binary.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess  # nosec
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

_logger = logging.getLogger(__name__)

_UNSAFE_VAULT_ID_CHARS = re.compile(r'[^A-Za-z0-9_-]+')

# Keys copied from the supervisor's own process env into every `ob` child's env.
# Deliberately narrow: `ob` needs to resolve its own binary/runtime (PATH) and a
# writable home for Node/npm config and cache (HOME); everything else the vault
# service process has (e.g. inter-service tokens, container settings) must not
# leak into the subprocess env. The real credential is always OBSIDIAN_AUTH_TOKEN,
# added separately per call — never one of these inherited keys.
_INHERITED_OB_ENV_KEYS = ('PATH', 'HOME', 'LANG', 'TZ')

# Bound on one-shot `ob sync-setup` / `ob sync` waits so a stalled Sync login or
# network hang can't block ensure_vault (and the HTTP request that triggered it)
# forever. Configurable per instance for tests.
_DEFAULT_ONE_SHOT_TIMEOUT_SECONDS = 120.0


class HeadlessSupervisor(Protocol):
    """Lifecycle contract for supervising one vault's headless Obsidian Sync.

    Implementations own starting/reusing and stopping whatever background
    sync process backs a vault_id, and report whether that vault's initial
    full sync has completed. `ensure_vault` must be idempotent: calling it
    again for an already-running vault_id is a no-op.
    """

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None) -> None:
        """Start (or reuse) headless sync for vault_id using the given Sync credential."""

    def stop_vault(self, vault_id: str) -> None:
        """Stop headless sync for vault_id and release any resources it holds."""

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True once vault_id's first full sync has completed."""

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id's continuous sync child process is currently running."""


class FakeSupervisor:
    """In-memory `HeadlessSupervisor` for tests — no subprocess or filesystem use.

    By default `ensure_vault` marks a vault ready immediately, matching the
    common test case where readiness doesn't matter. Pass
    `auto_complete=False` to keep vaults pending until the test explicitly
    calls `complete(vault_id)`, to exercise `sync_pending`-style code paths
    in callers (e.g. the HTTP layer's first-sync gate).
    """

    def __init__(self, *, auto_complete: bool = True) -> None:
        """Store the auto_complete policy and initialize empty vault state."""
        self._auto_complete = auto_complete
        self._credentials: dict[str, tuple[str, str | None]] = {}
        self._ready: set[str] = set()
        self._alive: set[str] = set()

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Register vault_id as running; mark it ready unless auto_complete=False.

        Idempotent for an already-registered vault_id: credentials are
        refreshed but existing readiness state is left untouched.
        """
        self._credentials[vault_id] = (auth_token, encryption_password)
        self._alive.add(vault_id)
        if self._auto_complete:
            self._ready.add(vault_id)

    def stop_vault(self, vault_id: str) -> None:
        """Discard vault_id's registration and readiness state."""
        self._credentials.pop(vault_id, None)
        self._ready.discard(vault_id)
        self._alive.discard(vault_id)

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True if vault_id has been marked ready (immediately or via complete())."""
        return vault_id in self._ready

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id is registered and hasn't been killed/stopped."""
        return vault_id in self._alive

    def kill(self, vault_id: str) -> None:
        """Simulate the continuous process dying without a full stop_vault, for liveness tests."""
        self._alive.discard(vault_id)

    def complete(self, vault_id: str) -> None:
        """Mark vault_id's initial sync complete; used by tests with auto_complete=False."""
        self._ready.add(vault_id)

    def is_running(self, vault_id: str) -> bool:
        """Return True if vault_id is currently registered (ensure_vault called, not yet stopped).

        Useful for API-layer tests that assert `stop_vault` actually tore
        down the vault rather than just clearing readiness.
        """
        return vault_id in self._credentials


class HeadlessSyncError(Exception):
    """Raised when an `ob` subprocess invocation exits with a non-zero status."""


class _SyncProcess(Protocol):
    """The subset of `subprocess.Popen` that `ObsidianHeadlessSupervisor` relies on.

    Kept minimal (rather than depending on `subprocess.Popen` directly) so
    tests can inject lightweight fake process objects for `popen_factory`
    without subclassing or wrapping the real `Popen`.
    """

    def wait(self, timeout: float | None = None) -> int:
        """Block until the process exits and return its exit code.

        Raises `subprocess.TimeoutExpired` if timeout elapses first, matching
        `subprocess.Popen.wait`'s contract.
        """

    def terminate(self) -> None:
        """Send a termination signal to the process."""

    def poll(self) -> int | None:
        """Return the exit code if the process has exited, else None (matches `Popen.poll`)."""


def _safe_vault_id(vault_id: str) -> str:
    """Sanitize vault_id into a filesystem-safe directory name.

    Replaces any run of characters outside `[A-Za-z0-9_-]` with `_`, so
    vault ids containing spaces, slashes, or other path-unsafe characters
    can't escape the vaults directory or collide with OS-reserved names.
    Falls back to a single `_` for an id that sanitizes to empty.
    """
    return _UNSAFE_VAULT_ID_CHARS.sub('_', vault_id) or '_'


class ObsidianHeadlessSupervisor:
    """Supervises `ob` (obsidian-headless) child processes, one working tree per vault.

    Each vault gets a working tree under `data_dir/vaults/{safe_vault_id}`.
    `ensure_vault` runs `ob sync-setup` then a one-shot `ob sync` to
    materialize the initial checkout before writing a `.sync-ready` marker
    file and starting a background `ob sync --continuous` child for ongoing
    sync. `is_initial_sync_complete` checks only for the marker file, so it
    never needs to shell out or parse `ob` output.

    The Sync auth token is passed via the `OBSIDIAN_AUTH_TOKEN` environment
    variable (per the headless docs' non-interactive login mechanism), never
    as a CLI argument, so it doesn't appear in process listings.
    """

    READY_MARKER_NAME = '.sync-ready'

    def __init__(
        self,
        data_dir: Path,
        *,
        ob_executable: str = 'ob',
        popen_factory: Callable[..., _SyncProcess] | None = None,
        one_shot_timeout_seconds: float = _DEFAULT_ONE_SHOT_TIMEOUT_SECONDS,
    ) -> None:
        """Store data_dir and the injectable Popen factory used for every `ob` invocation.

        `popen_factory` defaults to `subprocess.Popen` and is used both for
        the one-shot setup/sync calls (waited on synchronously via
        `.wait()`) and for the long-running continuous sync child, so tests
        can inject a single fake process factory covering all of them.
        `one_shot_timeout_seconds` bounds each one-shot wait; tests can pass
        a tiny value to exercise the timeout path without a real delay.
        """
        self._data_dir = data_dir
        self._ob_executable = ob_executable
        self._popen_factory: Callable[..., _SyncProcess] = popen_factory or subprocess.Popen
        self._one_shot_timeout_seconds = one_shot_timeout_seconds
        self._processes: dict[str, _SyncProcess] = {}

    def _build_ob_env(self, auth_token: str) -> dict[str, str]:
        """Build the minimal env passed to every `ob` child: a narrow inherited allowlist plus the Sync token.

        Never passes through the supervisor's full process env — see
        `_INHERITED_OB_ENV_KEYS` for why. `auth_token` is per-vault Sync
        credential material, always injected as `OBSIDIAN_AUTH_TOKEN`.
        """
        env = {key: os.environ[key] for key in _INHERITED_OB_ENV_KEYS if key in os.environ}
        env['OBSIDIAN_AUTH_TOKEN'] = auth_token
        return env

    def vault_dir(self, vault_id: str) -> Path:
        """Return the working tree path for vault_id (not guaranteed to exist).

        Public so callers (e.g. `main.py`'s `vault_root_for` wiring for
        `VaultFileService`) can resolve the same working tree the supervisor
        materializes, without duplicating the vault-id sanitization scheme.
        """
        return self._data_dir / 'vaults' / _safe_vault_id(vault_id)

    def _ready_marker(self, vault_id: str) -> Path:
        """Return the ready-marker file path for vault_id."""
        return self.vault_dir(vault_id) / self.READY_MARKER_NAME

    def _run_one_shot(self, args: list[str], *, env: dict[str, str]) -> int:
        """Start args via the Popen factory and block until it exits, returning its exit code.

        Bounded by `self._one_shot_timeout_seconds`: on timeout, terminates
        the child and re-raises `subprocess.TimeoutExpired` so the caller
        can leave the vault not-ready instead of hanging indefinitely.
        """
        process = self._popen_factory(args, env=env)
        try:
            return process.wait(timeout=self._one_shot_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            raise

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Materialize vault_id's working tree and start continuous headless sync.

        Idempotent: if a continuous sync child is already tracked for
        vault_id, returns without redoing setup or restarting it. Runs `ob
        sync-setup` then a one-shot `ob sync` to establish the initial
        checkout, writes the ready marker on success, then starts the
        continuous `ob sync --continuous` child. Raises `HeadlessSyncError`
        if either one-shot command exits non-zero (the ready marker is not
        written and no continuous child is started in that case). If either
        one-shot call exceeds `one_shot_timeout_seconds`, the child is
        killed and this method returns (not raises) leaving the vault
        not-ready; a later `ensure_vault` or status poll can retry/observe.
        """
        if vault_id in self._processes:
            return

        vault_dir = self.vault_dir(vault_id)
        vault_dir.mkdir(parents=True, exist_ok=True)
        env = self._build_ob_env(auth_token)

        setup_args = [self._ob_executable, 'sync-setup', '--vault', vault_id, '--path', str(vault_dir)]
        if encryption_password is not None:
            setup_args += ['--password', encryption_password]
        try:
            setup_exit_code = self._run_one_shot(setup_args, env=env)
        except subprocess.TimeoutExpired:
            _logger.warning(
                'ob sync-setup timed out for vault %r after %.0fs; leaving not ready',
                vault_id,
                self._one_shot_timeout_seconds,
            )
            return
        if setup_exit_code != 0:
            raise HeadlessSyncError(f'ob sync-setup failed for vault {vault_id!r} (exit {setup_exit_code})')

        initial_sync_args = [self._ob_executable, 'sync', '--path', str(vault_dir)]
        try:
            initial_sync_exit_code = self._run_one_shot(initial_sync_args, env=env)
        except subprocess.TimeoutExpired:
            _logger.warning(
                'initial ob sync timed out for vault %r after %.0fs; leaving not ready',
                vault_id,
                self._one_shot_timeout_seconds,
            )
            return
        if initial_sync_exit_code != 0:
            raise HeadlessSyncError(f'initial ob sync failed for vault {vault_id!r} (exit {initial_sync_exit_code})')

        self._ready_marker(vault_id).write_text('', encoding='utf-8')

        continuous_args = [self._ob_executable, 'sync', '--continuous', '--path', str(vault_dir)]
        self._processes[vault_id] = self._popen_factory(continuous_args, env=env)

    def stop_vault(self, vault_id: str) -> None:
        """Terminate vault_id's continuous sync child (if any) and clear its ready marker.

        Leaves the working tree itself on disk — callers that want to
        reclaim disk space should `rmtree` it separately once no agent
        references vault_id (e.g. after the last binding is released).
        """
        process = self._processes.pop(vault_id, None)
        if process is not None:
            process.terminate()
        marker = self._ready_marker(vault_id)
        if marker.exists():
            marker.unlink()

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True if vault_id's ready marker file exists on disk."""
        return self._ready_marker(vault_id).exists()

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id has a tracked continuous sync child that hasn't exited."""
        process = self._processes.get(vault_id)
        return process is not None and process.poll() is None
