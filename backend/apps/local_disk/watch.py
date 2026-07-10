# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Poll and debounce changes to local credential and agent YAML files."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from apps.keys.services.disk_sync import SyncReport, sync_keys_dir

from .agent_sync import sync_agents_dir

logger = logging.getLogger(__name__)

Fingerprint = tuple[int, int, str]


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
        return sync_agents_dir()
    return SyncReport()


class PollingWatcher:
    """Poll local provider trees and debounce synchronization per changed path."""

    def __init__(
        self,
        root: Path,
        *,
        interval: float = 1.0,
        debounce: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Capture the initial tree state and configure polling timings."""
        self.root = root.resolve()
        self.interval = interval
        self.debounce = debounce
        self.clock = clock
        self._pending: dict[Path, float] = {}
        self._snapshot = self._scan()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def _scan(self) -> dict[Path, Fingerprint]:
        """Return content-aware fingerprints for all provider YAML files."""
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

    def run(self) -> None:
        """Poll until stopped while containing failures to the watcher thread."""
        while not self._stopped.wait(self.interval):
            try:
                self.poll_once()
                self.flush_pending()
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
