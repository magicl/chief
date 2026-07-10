# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bootstrap and watch configured local disk providers from Django processes."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from apps.agents.services.disk_sync import sync_agents_dir
from apps.keys.services.disk_sync import SyncReport, sync_keys_dir
from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

Fingerprint = tuple[int, int, str]
_ORM_UNSAFE_COMMANDS = frozenset({'migrate', 'makemigrations', 'collectstatic'})
_DEFAULT_BOOT_SYNC_ATTEMPTS = 3
_DEFAULT_BOOT_SYNC_DELAY_S = 0.5
_DEFAULT_FULL_RESYNC_INTERVAL_S = 30.0


def resolve_local_root() -> Path | None:
    """Return the configured absolute local root, or None when unset."""
    raw = str(getattr(settings, 'CHIEF_LOCAL_DIR', '') or '').strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def keys_dir() -> Path | None:
    """Return the configured keys directory when local providers are enabled."""
    root = resolve_local_root()
    return None if root is None else root / 'keys'


def agents_dir() -> Path | None:
    """Return the configured agents directory when local providers are enabled."""
    root = resolve_local_root()
    return None if root is None else root / 'agents'


def sync_all() -> SyncReport:
    """Ingest keys before agents when the configured local root exists.

    Does not create ``keys/`` or ``agents/`` — operators own the tree layout.
    Missing provider directories are treated as empty by the sync helpers.
    """
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return SyncReport()

    # Agent materialization may reference credentials, so preserve this order.
    key_report = sync_keys_dir(root=root)
    agent_report = sync_agents_dir(root=root)
    return SyncReport(
        items=[*key_report.items, *agent_report.items],
        disabled=key_report.disabled + agent_report.disabled,
    )


def sync_path(path: Path, *, root: Path) -> SyncReport:
    """Synchronize the provider directory owning one changed or deleted path."""
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return SyncReport()
    if not relative.parts:
        return SyncReport()
    if relative.parts[0] == 'keys':
        return sync_keys_dir(root=root)
    if relative.parts[0] == 'agents':
        return sync_agents_dir(root=root)
    return SyncReport()


class PollingWatcher:
    """Poll local provider trees and debounce synchronization per changed path."""

    def __init__(
        self,
        root: Path,
        *,
        interval: float = 1.0,
        debounce: float = 0.3,
        full_resync_interval: float = _DEFAULT_FULL_RESYNC_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Capture the initial tree state and configure polling timings."""
        self.root = root.resolve()
        self.interval = interval
        self.debounce = debounce
        self.full_resync_interval = full_resync_interval
        self.clock = clock
        self._pending: dict[Path, float] = {}
        self._snapshot = self._scan()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        # Schedule the first full resync after one interval so boot sync can
        # finish first; a failed boot still recovers without waiting for edits.
        self._next_full_resync = self.clock() + self.full_resync_interval

    def _scan(self) -> dict[Path, Fingerprint]:
        """Return content-aware fingerprints for provider YAML files."""
        snapshot: dict[Path, Fingerprint] = {}
        for directory_name in ('keys', 'agents'):
            directory = self.root / directory_name
            if not directory.is_dir():
                continue
            for pattern in ('*.yaml', '*.yml'):
                for path in directory.glob(pattern):
                    try:
                        raw = path.read_bytes()
                        stat = path.stat()
                    except OSError:
                        continue
                    snapshot[path] = (stat.st_mtime_ns, stat.st_size, hashlib.sha256(raw).hexdigest())
        return snapshot

    def handle_fs_event(self, path: Path) -> None:
        """Queue or postpone synchronization for one changed path."""
        self._pending[path] = self.clock() + self.debounce

    def poll_once(self) -> None:
        """Detect one batch of created, changed, and deleted files."""
        current = self._scan()
        changed = {
            path for path in self._snapshot.keys() | current.keys() if self._snapshot.get(path) != current.get(path)
        }
        for path in changed:
            self.handle_fs_event(path)
        self._snapshot = current

    def flush_pending(self) -> None:
        """Synchronize paths whose debounce deadline has elapsed."""
        now = self.clock()
        due = [path for path, deadline in self._pending.items() if deadline <= now]
        for path in due:
            del self._pending[path]
            try:
                sync_path(path, root=self.root)
            # Watch threads must survive transient database failures.
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error('Local disk path sync failed for %s (%s)', path, type(exc).__name__)

    def maybe_full_resync(self) -> None:
        """Run a full provider sync when the periodic deadline has elapsed."""
        if self.full_resync_interval <= 0:
            return
        now = self.clock()
        if now < self._next_full_resync:
            return
        self._next_full_resync = now + self.full_resync_interval
        try:
            sync_all()
            _mark_root_synced(self.root)
        # Periodic resync must not stop change detection on transient failures.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error('Local disk full resync failed (%s)', type(exc).__name__)

    def run(self) -> None:
        """Poll until stopped while refreshing thread-local DB connections."""
        while not self._stopped.wait(self.interval):
            close_old_connections()
            try:
                self.poll_once()
                self.flush_pending()
                self.maybe_full_resync()
            # Preserve future polling after an unexpected scan failure.
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error('Local disk watcher poll failed (%s)', type(exc).__name__)

    def start(self) -> None:
        """Start one daemon polling thread when not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self.run, name='chief-local-disk-watch', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request watcher shutdown without blocking process exit."""
        self._stopped.set()


_watcher_lock = threading.Lock()
_watcher_state: list[PollingWatcher] = []


def start_watcher(root: Path) -> PollingWatcher:
    """Start or reuse the process-local watcher for the configured root."""
    resolved_root = root.resolve()
    with _watcher_lock:
        if _watcher_state and _watcher_state[0].root == resolved_root:
            _watcher_state[0].start()
            return _watcher_state[0]
        if _watcher_state:
            _watcher_state[0].stop()
        watcher = PollingWatcher(resolved_root)
        watcher.start()
        _watcher_state[:] = [watcher]
        return watcher


_bootstrap_lock = threading.Lock()
_synced_roots: set[Path] = set()


def _mark_root_synced(root: Path) -> None:
    """Record that one root completed a successful process-local sync."""
    with _bootstrap_lock:
        _synced_roots.add(root.resolve())


def _is_orm_unsafe_command() -> bool:
    """Return whether argv names a command that must defer ORM synchronization."""
    return any(argument in _ORM_UNSAFE_COMMANDS for argument in sys.argv)


def _is_runserver_parent() -> bool:
    """Return whether this is Django's autoreloader parent process."""
    return 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true'


def _sync_root_once(
    root: Path,
    *,
    attempts: int = _DEFAULT_BOOT_SYNC_ATTEMPTS,
    delay_s: float = _DEFAULT_BOOT_SYNC_DELAY_S,
) -> None:
    """Synchronize one root at most once after a successful process-local attempt.

    Retries a bounded number of times so a briefly unavailable database during
    compose start does not leave existing files unloaded until an edit or restart.
    """
    resolved = root.resolve()
    with _bootstrap_lock:
        if resolved in _synced_roots:
            return
    last_exc_name = 'Exception'
    for attempt in range(max(attempts, 1)):
        try:
            sync_all()
        # Startup must continue when the database is unavailable or not migrated.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_exc_name = type(exc).__name__
            if attempt + 1 >= attempts:
                logger.error('Local disk boot sync failed (%s)', last_exc_name)
                return
            time.sleep(delay_s)
            continue
        _mark_root_synced(resolved)
        return


def maybe_start_local_disk(*, force_watch: bool | None = None) -> None:
    """Sync local providers and optionally watch them without risking startup.

    Web processes pass ``force_watch=True`` and therefore start a watcher whenever
    a root is set. Multi-worker web deployments may run one watcher per worker;
    provider synchronization is idempotent. Worker processes watch only when
    ``CHIEF_LOCAL_WATCH`` is enabled.
    """
    root = resolve_local_root()
    if root is None:
        return
    if not root.is_dir():
        logger.warning('Configured local disk root is missing: %s', root)
        return
    if _is_orm_unsafe_command() or _is_runserver_parent():
        return

    _sync_root_once(root)
    should_watch = bool(getattr(settings, 'CHIEF_LOCAL_WATCH', False))
    if force_watch is not None:
        should_watch = force_watch
    if should_watch:
        start_watcher(root)


def sync_after_migrate(sender: object, **kwargs: object) -> None:
    """Run the deferred local provider sync after migrations have completed."""
    del sender, kwargs
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return
    _sync_root_once(root)
