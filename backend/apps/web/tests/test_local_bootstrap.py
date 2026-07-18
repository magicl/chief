# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import asyncio
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.keys.services.disk_sync import SyncItemResult, SyncReport
from apps.web.local_bootstrap import (
    PollingWatcher,
    agents_dir,
    keys_dir,
    maybe_start_local_disk,
    resolve_local_root,
    sync_after_migrate,
    sync_all,
)
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class FakeClock:
    """Provide deterministic monotonic time for debounce tests."""

    def __init__(self) -> None:
        """Start the fake clock at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake monotonic time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Advance fake monotonic time by the requested duration."""
        self.now += seconds


class TrackingLock:
    """Expose when a second thread attempts to enter a real lock."""

    def __init__(self) -> None:
        """Create the wrapped lock and acquisition rendezvous."""
        self._lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._attempts = 0
        self.second_attempt = threading.Event()

    def __enter__(self) -> None:
        """Record each acquisition attempt before waiting for ownership."""
        with self._count_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempt.set()
        self._lock.acquire()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Release the wrapped lock after the protected operation."""
        del exc_type, exc_value, traceback
        self._lock.release()


class TestLocalDiskPaths(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='')
    def test_resolve_root_unset_returns_none(self) -> None:
        """Return no root when local disk providers are disabled."""
        self.assertIsNone(resolve_local_root())

    @override_settings(CHIEF_LOCAL_DIR='/tmp/chief-local-test')
    def test_resolve_root_and_subdirs(self) -> None:
        """Resolve standard provider directories below the configured root."""
        root = resolve_local_root()
        assert root is not None
        self.assertEqual(root, Path('/tmp/chief-local-test').resolve())
        self.assertEqual(keys_dir(), root / 'keys')
        self.assertEqual(agents_dir(), root / 'agents')


class TestSyncAll(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='')
    def test_unset_root_is_inactive(self) -> None:
        """Leave both providers untouched when no local root is configured."""
        with (
            patch('apps.web.local_bootstrap.sync_keys_dir') as sync_keys,
            patch('apps.web.local_bootstrap.sync_agents_dir') as sync_agents,
        ):
            report = sync_all()

        self.assertEqual(report, SyncReport())
        sync_keys.assert_not_called()
        sync_agents.assert_not_called()

    def test_missing_root_is_inactive(self) -> None:
        """Leave both providers untouched when the configured root is absent."""
        with TemporaryDirectory() as parent:
            missing = Path(parent) / 'missing'
            with (
                override_settings(CHIEF_LOCAL_DIR=str(missing)),
                patch('apps.web.local_bootstrap.sync_keys_dir') as sync_keys,
                patch('apps.web.local_bootstrap.sync_agents_dir') as sync_agents,
            ):
                report = sync_all()

        self.assertEqual(report, SyncReport())
        sync_keys.assert_not_called()
        sync_agents.assert_not_called()

    def test_runs_keys_before_agents_and_combines_reports(self) -> None:
        """Synchronize keys first and return one combined provider report."""
        calls: list[str] = []
        key_report = SyncReport(items=[SyncItemResult('keys/key.yaml', True)], disabled=1)
        agent_report = SyncReport(items=[SyncItemResult('agents/agent.yaml', True)], disabled=2)

        def sync_keys(*, root: Path) -> SyncReport:
            """Record key provider ordering and return its sample report."""
            self.assertTrue(root.is_dir())
            calls.append('keys')
            return key_report

        def sync_agents(*, root: Path) -> SyncReport:
            """Record agent provider ordering and return its sample report."""
            self.assertTrue(root.is_dir())
            calls.append('agents')
            return agent_report

        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_keys_dir', side_effect=sync_keys),
                patch('apps.web.local_bootstrap.sync_agents_dir', side_effect=sync_agents),
            ):
                report = sync_all()

            # Operators own the tree; boot sync must not mkdir provider dirs.
            self.assertFalse((Path(root) / 'keys').exists())
            self.assertFalse((Path(root) / 'agents').exists())

        self.assertEqual(calls, ['keys', 'agents'])
        self.assertEqual(report.items, key_report.items + agent_report.items)
        self.assertEqual(report.disabled, 3)


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

            with patch('apps.web.local_bootstrap.sync_path') as sync_path:
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

            with patch('apps.web.local_bootstrap.sync_path') as sync_path:
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

    def test_poll_loop_refreshes_database_connections_first(self) -> None:
        """Refresh database connections before each watcher polling batch."""
        calls: list[str] = []
        with TemporaryDirectory() as root:
            watcher = PollingWatcher(Path(root), interval=0.0, full_resync_interval=0.0)

            def poll_once() -> None:
                """Record one poll and stop the watcher after this iteration."""
                calls.append('poll')
                watcher.stop()

            with (
                patch(
                    'apps.web.local_bootstrap.close_old_connections',
                    side_effect=lambda: calls.append('close'),
                ),
                patch.object(watcher, 'poll_once', side_effect=poll_once),
                patch.object(watcher, 'flush_pending', side_effect=lambda: calls.append('flush')),
            ):
                watcher.run()

        self.assertEqual(calls, ['close', 'poll', 'flush'])

    def test_periodic_full_resync_runs_after_interval(self) -> None:
        """Recover unloaded files by periodically re-running a full provider sync."""
        clock = FakeClock()
        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            watcher = PollingWatcher(root, full_resync_interval=10.0, clock=clock)

            with patch('apps.web.local_bootstrap.sync_all') as sync_all_mock:
                watcher.maybe_full_resync()
                sync_all_mock.assert_not_called()

                clock.advance(10.0)
                watcher.maybe_full_resync()
                sync_all_mock.assert_called_once_with()

                watcher.maybe_full_resync()
                sync_all_mock.assert_called_once_with()

                clock.advance(10.0)
                watcher.maybe_full_resync()

        self.assertEqual(sync_all_mock.call_count, 2)


class TestLocalDiskBootstrap(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='', CHIEF_LOCAL_WATCH=False)
    def test_unset_root_skips_bootstrap(self) -> None:
        """Skip initial synchronization and watcher startup without a root."""
        with (
            patch('apps.web.local_bootstrap.sync_all') as sync_all_mock,
            patch('apps.web.local_bootstrap.start_watcher') as start_watcher,
        ):
            maybe_start_local_disk(force_watch=True)

        sync_all_mock.assert_not_called()
        start_watcher.assert_not_called()

    @override_settings(CHIEF_LOCAL_WATCH=False)
    def test_existing_root_syncs_and_web_forces_watch(self) -> None:
        """Run boot synchronization and allow the web process to force watching."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_all') as sync_all_mock,
                patch('apps.web.local_bootstrap.start_watcher') as start_watcher,
            ):
                maybe_start_local_disk(force_watch=True)

            sync_all_mock.assert_called_once_with()
            start_watcher.assert_called_once_with(Path(root).resolve())

    def test_boot_sync_leaves_running_event_loop_thread(self) -> None:
        """Move ORM boot synchronization off a thread running an asyncio loop."""
        caller_thread = threading.get_ident()
        sync_threads: list[int] = []
        spawned_threads: list[threading.Thread] = []

        class RecordingThread(threading.Thread):
            """Retain spawned bootstrap threads so the test can join them."""

            def start(self) -> None:
                """Record this thread before starting it normally."""
                spawned_threads.append(self)
                super().start()

        def record_sync_thread() -> SyncReport:
            """Record where the simulated ORM synchronization executes."""
            sync_threads.append(threading.get_ident())
            return SyncReport()

        async def start_bootstrap() -> None:
            """Start local-disk bootstrap from an ASGI-style event loop."""
            maybe_start_local_disk(force_watch=False)

        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_all', side_effect=record_sync_thread),
                patch('apps.web.local_bootstrap.close_old_connections') as close_connections,
                patch('apps.web.local_bootstrap.threading.Thread', RecordingThread),
            ):
                asyncio.run(start_bootstrap())
                self.assertEqual(len(spawned_threads), 1)
                spawned_threads[0].join(timeout=1.0)
                self.assertFalse(spawned_threads[0].is_alive())

        self.assertEqual(len(sync_threads), 1)
        self.assertNotEqual(sync_threads[0], caller_thread)
        self.assertEqual(close_connections.call_count, 2)

    def test_concurrent_boot_calls_sync_once(self) -> None:
        """Coalesce boot calls that arrive before the first sync completes."""
        sync_started = threading.Event()
        release_sync = threading.Event()
        second_sync_started = threading.Event()
        sync_calls = 0
        sync_lock = TrackingLock()

        def blocking_sync() -> SyncReport:
            """Hold the first sync open so a concurrent caller can enter."""
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls > 1:
                second_sync_started.set()
            sync_started.set()
            release_sync.wait(timeout=1.0)
            return SyncReport()

        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_all', side_effect=blocking_sync),
                patch('apps.web.local_bootstrap._provider_sync_lock', sync_lock),
            ):
                first = threading.Thread(target=maybe_start_local_disk, kwargs={'force_watch': False})
                second = threading.Thread(target=maybe_start_local_disk, kwargs={'force_watch': False})
                first.start()
                self.assertTrue(sync_started.wait(timeout=1.0))
                second.start()
                self.assertTrue(sync_lock.second_attempt.wait(timeout=1.0))
                self.assertFalse(second_sync_started.is_set())
                release_sync.set()
                first.join(timeout=1.0)
                second.join(timeout=1.0)
                self.assertFalse(first.is_alive())
                self.assertFalse(second.is_alive())

        self.assertEqual(sync_calls, 1)

    def test_watcher_sync_waits_for_boot_sync(self) -> None:
        """Serialize watcher writes behind an in-progress boot synchronization."""
        clock = FakeClock()
        boot_started = threading.Event()
        release_boot = threading.Event()
        path_synced = threading.Event()
        sync_lock = TrackingLock()

        def blocking_boot_sync() -> SyncReport:
            """Hold boot sync open while the watcher attempts a path sync."""
            boot_started.set()
            release_boot.wait(timeout=1.0)
            return SyncReport()

        def record_path_sync(path: Path, *, root: Path) -> SyncReport:
            """Record when the watcher reaches provider synchronization."""
            del path, root
            path_synced.set()
            return SyncReport()

        with TemporaryDirectory() as root_text:
            root = Path(root_text)
            watcher = PollingWatcher(root, debounce=0, clock=clock)
            watcher.handle_fs_event(root / 'agents' / 'example.yaml')
            with (
                override_settings(CHIEF_LOCAL_DIR=root_text),
                patch('apps.web.local_bootstrap.sync_all', side_effect=blocking_boot_sync),
                patch('apps.web.local_bootstrap.sync_path', side_effect=record_path_sync),
                patch('apps.web.local_bootstrap._provider_sync_lock', sync_lock),
            ):
                boot_thread = threading.Thread(target=maybe_start_local_disk, kwargs={'force_watch': False})
                watch_thread = threading.Thread(target=watcher.flush_pending)
                boot_thread.start()
                self.assertTrue(boot_started.wait(timeout=1.0))
                watch_thread.start()
                self.assertTrue(sync_lock.second_attempt.wait(timeout=1.0))
                self.assertFalse(path_synced.is_set())
                release_boot.set()
                boot_thread.join(timeout=1.0)
                watch_thread.join(timeout=1.0)
                self.assertFalse(boot_thread.is_alive())
                self.assertFalse(watch_thread.is_alive())

        self.assertTrue(path_synced.is_set())

    @override_settings(CHIEF_LOCAL_WATCH=True)
    def test_worker_flag_enables_watch(self) -> None:
        """Start watching from a worker only when explicitly configured."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_all'),
                patch('apps.web.local_bootstrap.start_watcher') as start_watcher,
            ):
                maybe_start_local_disk()

        start_watcher.assert_called_once_with(Path(root).resolve())

    def test_migrate_argv_skips_boot_sync(self) -> None:
        """Avoid ORM synchronization while migration commands are running."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch.object(sys, 'argv', ['manage.py', 'migrate']),
                patch('apps.web.local_bootstrap.sync_all') as sync_all_mock,
                patch('apps.web.local_bootstrap.start_watcher') as start_watcher,
            ):
                maybe_start_local_disk(force_watch=True)

        sync_all_mock.assert_not_called()
        start_watcher.assert_not_called()

    def test_sync_failure_does_not_escape(self) -> None:
        """Contain boot synchronization failures so Django startup can continue."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch(
                    'apps.web.local_bootstrap.sync_all',
                    side_effect=RuntimeError('database unavailable'),
                ),
                patch('apps.web.local_bootstrap.time.sleep'),
                patch('apps.web.local_bootstrap.start_watcher') as start_watcher,
                patch('apps.web.local_bootstrap.logger'),
            ):
                maybe_start_local_disk(force_watch=True)

        start_watcher.assert_called_once_with(Path(root).resolve())

    def test_boot_sync_retries_transient_failure(self) -> None:
        """Retry boot sync a bounded number of times before giving up."""
        attempts = {'count': 0}

        def flaky_sync() -> SyncReport:
            """Fail twice, then succeed, to exercise bounded boot retries."""
            attempts['count'] += 1
            if attempts['count'] < 3:
                raise RuntimeError('database unavailable')
            return SyncReport()

        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.web.local_bootstrap.sync_all', side_effect=flaky_sync),
                patch('apps.web.local_bootstrap.time.sleep') as sleep_mock,
                patch('apps.web.local_bootstrap.start_watcher'),
            ):
                maybe_start_local_disk(force_watch=True)

        self.assertEqual(attempts['count'], 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_post_migrate_sync_uses_safe_path(self) -> None:
        """Run the deferred sync after migrations even under migrate argv."""
        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch.object(sys, 'argv', ['manage.py', 'migrate']),
                patch('apps.web.local_bootstrap.sync_all') as sync_all_mock,
            ):
                sync_after_migrate(sender=None)

        sync_all_mock.assert_called_once_with()
