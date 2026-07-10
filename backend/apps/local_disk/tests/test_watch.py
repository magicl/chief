# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.local_disk.bootstrap import maybe_start_local_disk
from apps.local_disk.watch import PollingWatcher
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class FakeClock:
    """Provide deterministic monotonic time for debounce tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake monotonic time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Advance fake monotonic time by the requested duration."""
        self.now += seconds


class TestPollingWatcher(OTestCase):
    def test_rapid_events_for_one_path_coalesce(self) -> None:
        """Apply only one sync after repeated events within the debounce window."""
        clock = FakeClock()
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            (root / 'keys').mkdir()
            (root / 'agents').mkdir()
            path = root / 'keys' / 'key.yaml'
            watcher = PollingWatcher(root, debounce=0.3, clock=clock)

            with patch('apps.local_disk.watch.sync_path') as sync_path:
                watcher.handle_fs_event(path)
                clock.advance(0.2)
                watcher.handle_fs_event(path)
                clock.advance(0.29)
                watcher.flush_pending()
                sync_path.assert_not_called()

                clock.advance(0.01)
                watcher.flush_pending()

        sync_path.assert_called_once_with(path, root=root)

    def test_poll_detects_changed_and_deleted_files(self) -> None:
        """Queue file content changes and deletions discovered by polling."""
        clock = FakeClock()
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            keys = root / 'keys'
            agents = root / 'agents'
            keys.mkdir()
            agents.mkdir()
            path = keys / 'key.yaml'
            path.write_text('first', encoding='utf-8')
            watcher = PollingWatcher(root, debounce=0.3, clock=clock)

            with patch('apps.local_disk.watch.sync_path') as sync_path:
                path.write_text('second', encoding='utf-8')
                watcher.poll_once()
                clock.advance(0.3)
                watcher.flush_pending()
                path.unlink()
                watcher.poll_once()
                clock.advance(0.3)
                watcher.flush_pending()

        self.assertEqual(sync_path.call_count, 2)
        sync_path.assert_called_with(path, root=root)


class TestLocalDiskBootstrap(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='', CHIEF_LOCAL_WATCH=False)
    def test_unset_root_skips_bootstrap(self) -> None:
        """Skip initial synchronization and watcher startup without a root."""
        with (
            patch('apps.local_disk.bootstrap.sync_all') as sync_all,
            patch('apps.local_disk.bootstrap.start_watcher') as start_watcher,
        ):
            maybe_start_local_disk(force_watch=True)

        sync_all.assert_not_called()
        start_watcher.assert_not_called()

    @override_settings(CHIEF_LOCAL_WATCH=False)
    def test_existing_root_syncs_and_web_forces_watch(self) -> None:
        """Run boot synchronization and allow the web process to force watching."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.local_disk.bootstrap.sync_all') as sync_all,
                patch('apps.local_disk.bootstrap.start_watcher') as start_watcher,
            ):
                maybe_start_local_disk(force_watch=True)

            sync_all.assert_called_once_with()
            start_watcher.assert_called_once_with(Path(root).resolve())

    @override_settings(CHIEF_LOCAL_WATCH=True)
    def test_worker_flag_enables_watch(self) -> None:
        """Start watching from a non-web process when explicitly configured."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.local_disk.bootstrap.sync_all'),
                patch('apps.local_disk.bootstrap.start_watcher') as start_watcher,
            ):
                maybe_start_local_disk()

        start_watcher.assert_called_once_with(Path(root).resolve())
