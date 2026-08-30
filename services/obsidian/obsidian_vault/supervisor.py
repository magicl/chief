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
import threading
from collections.abc import Callable
from enum import Enum
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


class VaultSyncState(str, Enum):
    """First-sync lifecycle state of one vault's checkout.

    Callers use this to decide whether a checkout may be read (possibly
    partial) or written. It is deliberately coarser than the ready marker:
    `READY` means first full Sync finished, while `SYNCING` and `PARTIAL`
    both describe a tree that exists but may be incomplete.

    - `NOT_STARTED`: no attempt has been made, or the vault was stopped.
    - `SYNCING`: an `ensure_vault` attempt is in flight right now.
    - `PARTIAL`: an attempt ended without completing (one-shot timeout), so
      whatever was fetched is on disk but first full Sync did not finish.
    - `FAILED`: `ob` exited non-zero — the checkout is not trustworthy.
    - `READY`: first full Sync completed and continuous sync is running.

    This state is in-process only; the status API intentionally exposes only
    readiness and process-liveness fields, not this enum.
    """

    NOT_STARTED = 'not_started'
    SYNCING = 'syncing'
    PARTIAL = 'partial'
    FAILED = 'failed'
    READY = 'ready'


class HeadlessSupervisor(Protocol):
    """Lifecycle contract for supervising one vault's headless Obsidian Sync.

    Implementations own starting/reusing and stopping whatever background
    sync process backs a vault_id, and report whether that vault's initial
    full sync has completed. `ensure_vault` must be idempotent: calling it
    again for an already-running vault_id is a no-op.

    Implementations must be safe to call concurrently from multiple threads
    (the vault service is a threaded HTTP app) and must not make `sync_state`
    or `is_initial_sync_complete` wait on an in-flight sync attempt.
    """

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None) -> None:
        """Start (or reuse) headless sync for vault_id using the given Sync credential."""

    def stop_vault(self, vault_id: str) -> None:
        """Stop headless sync for vault_id and release any resources it holds."""

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True once vault_id's first full sync has completed."""

    def sync_state(self, vault_id: str) -> VaultSyncState:
        """Return the current first-sync lifecycle state for vault_id.

        Must return promptly even while an `ensure_vault` attempt is running,
        so read paths can distinguish "not started" from "partial tree".
        """

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id's continuous sync child process is currently running."""


class FakeSupervisor:
    """In-memory `HeadlessSupervisor` for tests — no subprocess or filesystem use.

    By default `ensure_vault` marks a vault ready immediately, matching the
    common test case where readiness doesn't matter. Pass
    `auto_complete=False` to keep vaults pending until the test explicitly
    calls `complete(vault_id)`, to exercise `sync_pending`-style code paths
    in callers (e.g. the HTTP layer's first-sync gate).

    Sync state mirrors the real supervisor's contract: a pending vault sits
    in `PARTIAL` (a checkout exists but first full Sync has not finished),
    `complete` moves it to `READY`, and `fail` models a hard `ob` failure.
    State is guarded by the same short lock as the rest of the vault maps so
    callers may drive the fake from several threads.
    """

    def __init__(self, *, auto_complete: bool = True) -> None:
        """Store the auto_complete policy and initialize empty vault state."""
        self._auto_complete = auto_complete
        self._lock = threading.Lock()
        self._credentials: dict[str, tuple[str, str | None]] = {}
        self._ready: set[str] = set()
        self._alive: set[str] = set()
        self._states: dict[str, VaultSyncState] = {}

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Register vault_id as running; mark it ready unless auto_complete=False.

        Idempotent for an already-registered vault_id: credentials are
        refreshed but existing readiness state is left untouched, so a vault
        already in `READY` stays there even under `auto_complete=False`.
        """
        with self._lock:
            self._credentials[vault_id] = (auth_token, encryption_password)
            self._alive.add(vault_id)
            if self._auto_complete:
                self._ready.add(vault_id)
            if vault_id in self._ready:
                self._states[vault_id] = VaultSyncState.READY
            else:
                self._states[vault_id] = VaultSyncState.PARTIAL

    def stop_vault(self, vault_id: str) -> None:
        """Discard vault_id's registration, readiness, and sync state."""
        with self._lock:
            self._credentials.pop(vault_id, None)
            self._ready.discard(vault_id)
            self._alive.discard(vault_id)
            self._states.pop(vault_id, None)

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True if vault_id has been marked ready (immediately or via complete())."""
        with self._lock:
            return vault_id in self._ready

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id is registered and hasn't been killed/stopped."""
        with self._lock:
            return vault_id in self._alive

    def sync_state(self, vault_id: str) -> VaultSyncState:
        """Return vault_id's simulated first-sync state (NOT_STARTED when unknown or stopped)."""
        with self._lock:
            return self._states.get(vault_id, VaultSyncState.NOT_STARTED)

    def kill(self, vault_id: str) -> None:
        """Simulate the continuous process dying without a full stop_vault, for liveness tests.

        Leaves readiness and sync state alone: as in the real supervisor, a
        dead continuous child does not un-complete the first full Sync.
        """
        with self._lock:
            self._alive.discard(vault_id)

    def complete(self, vault_id: str) -> None:
        """Mark vault_id's initial sync complete; used by tests with auto_complete=False."""
        with self._lock:
            self._ready.add(vault_id)
            self._states[vault_id] = VaultSyncState.READY

    def fail(self, vault_id: str) -> None:
        """Simulate a hard `ob` failure for vault_id: not ready, not alive, state FAILED.

        Test-control helper alongside `complete`/`kill`, so callers can
        exercise the unusable-checkout path without a real subprocess. The
        registration is kept (like `kill`) so the vault stays bound and the
        failure is what callers observe.
        """
        with self._lock:
            self._ready.discard(vault_id)
            self._alive.discard(vault_id)
            self._states[vault_id] = VaultSyncState.FAILED

    def is_running(self, vault_id: str) -> bool:
        """Return True if vault_id is currently registered (ensure_vault called, not yet stopped).

        Useful for API-layer tests that assert `stop_vault` actually tore
        down the vault rather than just clearing readiness.
        """
        with self._lock:
            return vault_id in self._credentials


class HeadlessSyncError(Exception):
    """Raised when an `ob` subprocess invocation exits with a non-zero status."""


class _AttemptAbandoned(Exception):
    """Internal signal that a first-sync attempt no longer owns its vault.

    Raised inside `ObsidianHeadlessSupervisor` when `stop_vault` (or, after
    that stop, a newer `ensure_vault`) invalidated the running attempt's token.
    The abandoned attempt must then stop launching `ob` processes and publish
    nothing. Never escapes `ensure_vault`.
    """


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

    **Concurrency.** `_lock` guards the per-vault state, token, and process
    maps only, and is held for short, non-blocking critical sections. `ob`
    waits always run with the lock released, so `sync_state`,
    `is_process_alive`, and (in callers) partial reads never queue behind a
    first sync.

    **Attempt ownership.** Each `ensure_vault` that actually starts work
    claims the vault by moving it to `SYNCING` and taking a fresh monotonic
    *attempt token*. That token — not the `SYNCING` state — is the attempt's
    identity: a stop followed by a new `ensure_vault` puts the vault back in
    `SYNCING` under a *different* token, so the earlier attempt (which may
    still be blocked in `ob`) can no longer be mistaken for the current one.
    An attempt may only touch vault state, the ready marker, or the process
    maps while it still owns the vault (`_owns_attempt`); otherwise it
    discards its result. Every attempt leaves the vault in a terminal state
    (`PARTIAL`, `FAILED`, or `READY`) or hands ownership over to whoever
    invalidated it — it never abandons the vault in `SYNCING`, which would
    make all later `ensure_vault` calls short-circuit forever.
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
        self._lock = threading.Lock()
        self._processes: dict[str, _SyncProcess] = {}
        self._states: dict[str, VaultSyncState] = {}
        # Attempt token of the `ensure_vault` call that currently owns each
        # vault, and the one-shot child that attempt is waiting on (kept with
        # its token so a stop never terminates a later attempt's process).
        self._attempt_tokens: dict[str, int] = {}
        self._active_one_shots: dict[str, tuple[int, _SyncProcess]] = {}
        self._last_attempt_token = 0

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

    def _owns_attempt(self, vault_id: str, token: int) -> bool:
        """Return True if the attempt identified by token still owns vault_id.

        Assumes `self._lock` is already held. Ownership needs both a `SYNCING`
        state and a matching token: after stop + re-ensure the state is
        `SYNCING` again under a newer token, and the older attempt must not be
        able to act on the newer one's behalf.
        """
        return self._states.get(vault_id) == VaultSyncState.SYNCING and self._attempt_tokens.get(vault_id) == token

    def _claim_attempt(self, vault_id: str) -> int | None:
        """Try to become the single owner of vault_id's first-sync attempt.

        Returns a fresh attempt token after moving the vault into `SYNCING`,
        or None when another thread is already syncing it (`SYNCING`) or it is
        already `READY` — in which case the caller must not launch a second
        set of `ob` processes. `PARTIAL` and `FAILED` are retryable, so they
        claim a fresh attempt.
        """
        with self._lock:
            state = self._states.get(vault_id, VaultSyncState.NOT_STARTED)
            if state in (VaultSyncState.SYNCING, VaultSyncState.READY):
                return None
            self._last_attempt_token += 1
            token = self._last_attempt_token
            self._states[vault_id] = VaultSyncState.SYNCING
            self._attempt_tokens[vault_id] = token
            return token

    def _end_attempt(self, vault_id: str, token: int, state: VaultSyncState) -> None:
        """Record a non-ready outcome (`PARTIAL` / `FAILED`) for an attempt that just finished.

        No-op unless the attempt still owns the vault, so a stop (or the newer
        attempt that followed it) is never overwritten by a stale result.
        Releases ownership, making the vault claimable again for a retry.
        """
        with self._lock:
            if not self._owns_attempt(vault_id, token):
                return
            self._states[vault_id] = state
            self._attempt_tokens.pop(vault_id, None)

    def _run_one_shot(self, vault_id: str, token: int, args: list[str], *, env: dict[str, str]) -> int:
        """Start args via the Popen factory and block until it exits, returning its exit code.

        Bounded by `self._one_shot_timeout_seconds`: on timeout, terminates
        the child and re-raises `subprocess.TimeoutExpired` so the caller
        can leave the vault not-ready instead of hanging indefinitely.

        The child is published as vault_id's active one-shot (tagged with this
        attempt's token) so `stop_vault` can terminate it mid-wait, and is
        unpublished on the way out. Raises `_AttemptAbandoned` instead of
        starting — or continuing to own — a child once this attempt has lost
        the vault. The wait itself never runs under `self._lock`.
        """
        with self._lock:
            if not self._owns_attempt(vault_id, token):
                raise _AttemptAbandoned(vault_id)

        process = self._popen_factory(args, env=env)

        with self._lock:
            still_owner = self._owns_attempt(vault_id, token)
            if still_owner:
                self._active_one_shots[vault_id] = (token, process)
        if not still_owner:
            # Lost the vault in the window between the check and the spawn:
            # clean up the child we just started rather than leaking it.
            process.terminate()
            raise _AttemptAbandoned(vault_id)

        try:
            exit_code = process.wait(timeout=self._one_shot_timeout_seconds)
            with self._lock:
                if not self._owns_attempt(vault_id, token):
                    raise _AttemptAbandoned(vault_id)
            return exit_code
        except subprocess.TimeoutExpired:
            process.terminate()
            raise
        finally:
            with self._lock:
                active = self._active_one_shots.get(vault_id)
                if active is not None and active[0] == token:
                    del self._active_one_shots[vault_id]

    def _run_attempt(self, vault_id: str, token: int, *, auth_token: str, encryption_password: str | None) -> None:
        """Run one full first-sync attempt for vault_id on behalf of the given attempt token.

        Performs `ob sync-setup`, the one-shot `ob sync`, and — only while
        this attempt still owns the vault — publishes the ready marker,
        continuous child, and `READY`. Records `PARTIAL` on a one-shot
        timeout, raises `HeadlessSyncError` on a non-zero exit (the caller
        turns that into `FAILED`), and returns quietly when the attempt is
        abandoned. Assumes the caller already claimed `token`.
        """
        vault_dir = self.vault_dir(vault_id)
        vault_dir.mkdir(parents=True, exist_ok=True)
        env = self._build_ob_env(auth_token)

        setup_args = [self._ob_executable, 'sync-setup', '--vault', vault_id, '--path', str(vault_dir)]
        if encryption_password is not None:
            setup_args += ['--password', encryption_password]
        try:
            setup_exit_code = self._run_one_shot(vault_id, token, setup_args, env=env)
        except subprocess.TimeoutExpired:
            _logger.warning(
                'ob sync-setup timed out for vault %r after %.0fs; leaving not ready',
                vault_id,
                self._one_shot_timeout_seconds,
            )
            self._end_attempt(vault_id, token, VaultSyncState.PARTIAL)
            return
        if setup_exit_code != 0:
            raise HeadlessSyncError(f'ob sync-setup failed for vault {vault_id!r} (exit {setup_exit_code})')

        initial_sync_args = [self._ob_executable, 'sync', '--path', str(vault_dir)]
        try:
            initial_sync_exit_code = self._run_one_shot(vault_id, token, initial_sync_args, env=env)
        except subprocess.TimeoutExpired:
            _logger.warning(
                'initial ob sync timed out for vault %r after %.0fs; leaving not ready',
                vault_id,
                self._one_shot_timeout_seconds,
            )
            self._end_attempt(vault_id, token, VaultSyncState.PARTIAL)
            return
        if initial_sync_exit_code != 0:
            raise HeadlessSyncError(f'initial ob sync failed for vault {vault_id!r} (exit {initial_sync_exit_code})')

        continuous_args = [self._ob_executable, 'sync', '--continuous', '--path', str(vault_dir)]
        with self._lock:
            if not self._owns_attempt(vault_id, token):
                return
            # Marker first, continuous child second, with the marker rolled
            # back if the child cannot start: a marker on disk must never
            # advertise readiness for a vault that has nothing syncing it.
            marker = self._ready_marker(vault_id)
            marker.write_text('', encoding='utf-8')
            try:
                process = self._popen_factory(continuous_args, env=env)
            except BaseException:
                marker.unlink(missing_ok=True)
                raise
            self._processes[vault_id] = process
            self._states[vault_id] = VaultSyncState.READY
            self._attempt_tokens.pop(vault_id, None)

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Materialize vault_id's working tree and start continuous headless sync.

        Idempotent and single-flight: returns immediately if the vault is
        already `READY` or another thread's attempt is in flight (`SYNCING`),
        without redoing setup or starting a second process. Runs `ob
        sync-setup` then a one-shot `ob sync` to establish the initial
        checkout, writes the ready marker on success, then starts the
        continuous `ob sync --continuous` child. Raises `HeadlessSyncError`
        if either one-shot command exits non-zero (state becomes `FAILED`;
        the ready marker is not written and no continuous child is started).
        If either one-shot call exceeds `one_shot_timeout_seconds`, the child
        is killed and this method returns (not raises) leaving the vault
        `PARTIAL`; a later `ensure_vault` or status poll can retry/observe.

        The state lock is held only to claim the attempt and to publish its
        outcome — never across a subprocess wait — so concurrent callers and
        state readers are not blocked by `ob`. A `stop_vault` during the
        attempt discards whatever this call was about to publish, and any
        failure marks the vault `FAILED` rather than stranding it in
        `SYNCING`, so a later call can always retry.
        """
        token = self._claim_attempt(vault_id)
        if token is None:
            return

        try:
            self._run_attempt(vault_id, token, auth_token=auth_token, encryption_password=encryption_password)
        except _AttemptAbandoned:
            _logger.info('first-sync attempt for vault %r was stopped or superseded; discarding it', vault_id)
        except BaseException:
            # Anything else — a non-zero `ob` exit, an unusable data dir, a
            # missing `ob` binary from the Popen factory — is a hard failure of
            # this attempt. Record it before re-raising so the vault does not
            # stay `SYNCING` and block every future ensure.
            self._end_attempt(vault_id, token, VaultSyncState.FAILED)
            raise

    def stop_vault(self, vault_id: str) -> None:
        """Terminate vault_id's sync children (if any), clear its marker, and reset state.

        Leaves the working tree itself on disk — callers that want to
        reclaim disk space should `rmtree` it separately once no agent
        references vault_id (e.g. after the last binding is released).

        Invalidates any in-flight attempt's ownership and terminates both the
        continuous child and the one-shot the attempt is currently waiting on,
        so a first sync does not keep running against a stopped vault. State
        reset, marker removal, and process detachment happen atomically under
        the lock; the `terminate` calls are made after releasing it, since
        this class must never hold the lock while touching a process beyond
        the maps (a `terminate` implementation is free to call back in, and
        callers may concurrently be in `sync_state`).
        """
        with self._lock:
            self._states.pop(vault_id, None)
            self._attempt_tokens.pop(vault_id, None)
            doomed: list[_SyncProcess] = []
            continuous_process = self._processes.pop(vault_id, None)
            if continuous_process is not None:
                doomed.append(continuous_process)
            active_one_shot = self._active_one_shots.pop(vault_id, None)
            if active_one_shot is not None:
                doomed.append(active_one_shot[1])
            marker = self._ready_marker(vault_id)
            if marker.exists():
                marker.unlink()

        for process in doomed:
            process.terminate()

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True if vault_id's ready marker file exists on disk."""
        return self._ready_marker(vault_id).exists()

    def sync_state(self, vault_id: str) -> VaultSyncState:
        """Return vault_id's first-sync lifecycle state (NOT_STARTED when unknown or stopped).

        Only takes the short state lock, so it stays responsive while an
        attempt waits on `ob`.
        """
        with self._lock:
            return self._states.get(vault_id, VaultSyncState.NOT_STARTED)

    def is_process_alive(self, vault_id: str) -> bool:
        """Return True if vault_id has a tracked continuous sync child that hasn't exited."""
        with self._lock:
            process = self._processes.get(vault_id)
        return process is not None and process.poll() is None
