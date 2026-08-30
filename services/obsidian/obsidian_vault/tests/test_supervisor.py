# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import contextlib
import subprocess
import threading
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from obsidian_vault.supervisor import (
    FakeSupervisor,
    HeadlessSyncError,
    ObsidianHeadlessSupervisor,
    VaultSyncState,
)

# Upper bound on every cross-thread wait in this module. Concurrency tests must
# fail with a readable assertion instead of hanging the suite when a locking
# regression keeps a thread blocked forever.
_EVENT_TIMEOUT_SECONDS = 5.0


class TestFakeSupervisor(unittest.TestCase):
    def test_ensure_vault_completes_immediately_by_default(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(supervisor.is_initial_sync_complete('Personal'))
        self.assertTrue(supervisor.is_running('Personal'))

    def test_unknown_vault_is_not_complete(self) -> None:
        supervisor = FakeSupervisor()
        self.assertFalse(supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(supervisor.is_running('Personal'))

    def test_ensure_vault_stays_pending_when_auto_complete_disabled(self) -> None:
        supervisor = FakeSupervisor(auto_complete=False)
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertFalse(supervisor.is_initial_sync_complete('Personal'))
        self.assertTrue(supervisor.is_running('Personal'))

    def test_complete_marks_pending_vault_ready(self) -> None:
        supervisor = FakeSupervisor(auto_complete=False)
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.complete('Personal')
        self.assertTrue(supervisor.is_initial_sync_complete('Personal'))

    def test_stop_vault_removes_registration_and_readiness(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.stop_vault('Personal')
        self.assertFalse(supervisor.is_running('Personal'))
        self.assertFalse(supervisor.is_initial_sync_complete('Personal'))

    def test_stop_vault_on_unknown_vault_does_not_raise(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.stop_vault('Missing')

    def test_ensure_vault_is_idempotent_for_readiness(self) -> None:
        supervisor = FakeSupervisor(auto_complete=False)
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.complete('Personal')
        supervisor.ensure_vault('Personal', auth_token='tok2', encryption_password=None)
        self.assertTrue(supervisor.is_initial_sync_complete('Personal'))

    def test_ensure_vault_marks_process_alive(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(supervisor.is_process_alive('Personal'))

    def test_unknown_vault_is_not_alive(self) -> None:
        supervisor = FakeSupervisor()
        self.assertFalse(supervisor.is_process_alive('Personal'))

    def test_stop_vault_clears_liveness(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.stop_vault('Personal')
        self.assertFalse(supervisor.is_process_alive('Personal'))

    def test_kill_clears_liveness_without_stopping(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.kill('Personal')
        self.assertFalse(supervisor.is_process_alive('Personal'))
        self.assertTrue(supervisor.is_running('Personal'))

    def test_unknown_vault_reports_not_started(self) -> None:
        supervisor = FakeSupervisor()
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)

    def test_auto_complete_ensure_reports_ready(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_pending_fake_reports_partial(self) -> None:
        supervisor = FakeSupervisor(auto_complete=False)
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

    def test_complete_moves_pending_vault_to_ready(self) -> None:
        supervisor = FakeSupervisor(auto_complete=False)
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.complete('Personal')
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_fail_marks_vault_failed_and_not_ready(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.fail('Personal')
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.FAILED)
        self.assertFalse(supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(supervisor.is_process_alive('Personal'))

    def test_ensure_vault_after_fail_can_reach_ready_again(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.fail('Personal')
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_stop_resets_sync_state(self) -> None:
        supervisor = FakeSupervisor()
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        supervisor.stop_vault('Personal')
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)


class _FakeProcess:
    """Stand-in for `subprocess.Popen` that records calls and lets tests script exit codes."""

    def __init__(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        exit_code: int = 0,
        hangs: bool = False,
        entered_wait: threading.Event | None = None,
        release_wait: threading.Event | None = None,
        terminated_wait_resumed: threading.Event | None = None,
        allow_terminated_return: threading.Event | None = None,
    ) -> None:
        self.args = args
        self.env = env
        self._exit_code = exit_code
        self._hangs = hangs
        self._entered_wait = entered_wait
        self._release_wait = release_wait
        self._terminated_wait_resumed = terminated_wait_resumed
        self._allow_terminated_return = allow_terminated_return
        self._alive = True
        self.terminated = False
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        """Return the scripted exit code, or raise TimeoutExpired if scripted to hang.

        When gate events were supplied, signal `entered_wait` and block until
        the test sets `release_wait`. That models a real `ob` one-shot holding
        its caller for a long time, so a test can inspect supervisor state (and
        issue a second `ensure_vault`) while the first attempt is mid-flight.
        """
        self.wait_timeouts.append(timeout)
        if self._entered_wait is not None:
            self._entered_wait.set()
        if self._release_wait is not None and not self._release_wait.wait(timeout=_EVENT_TIMEOUT_SECONDS):
            raise AssertionError(f'blocked fake process {self.args!r} was never released')
        if self.terminated:
            if self._terminated_wait_resumed is not None:
                self._terminated_wait_resumed.set()
            if self._allow_terminated_return is not None and not self._allow_terminated_return.wait(
                timeout=_EVENT_TIMEOUT_SECONDS
            ):
                raise AssertionError(f'terminated fake process {self.args!r} was not allowed to return')
            return -15
        if self._hangs:
            raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout or 0)
        self._alive = False
        return self._exit_code

    def terminate(self) -> None:
        """Record termination and unblock a process parked in wait()."""
        self.terminated = True
        self._alive = False
        if self._release_wait is not None:
            self._release_wait.set()

    def poll(self) -> int | None:
        """Return the scripted exit code once not alive, else None (still running)."""
        return None if self._alive else self._exit_code

    def mark_exited(self) -> None:
        """Simulate the process dying on its own (no `terminate()` call), for liveness tests."""
        self._alive = False


class _FakePopenFactory:
    """Injectable `popen_factory` that records every invocation and lets tests script failures.

    By default every started process exits 0 (`wait()` returns 0). Call
    `fail_on(*binary_and_subcommand)` to make matching invocations exit
    non-zero instead, to exercise `HeadlessSyncError` paths. Call
    `hang_on(subcommand)` to make matching one-shot invocations time out
    instead, to exercise the timeout path. Call `block_on(subcommand)` to make
    the next matching one-shot park inside `wait()` until the test releases it,
    to exercise concurrent access while an attempt is in flight. Call
    `raise_on(...)` to make the factory itself blow up the way a real `Popen`
    does when the `ob` binary cannot be executed.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.processes: list[_FakeProcess] = []
        self._failing_subcommands: set[str] = set()
        self._hanging_subcommands: set[str] = set()
        self._raising_targets: set[tuple[str, bool]] = set()
        self._blocking_subcommand: str | None = None
        self._entered_wait: threading.Event | None = None
        self._release_wait: threading.Event | None = None
        self._terminated_wait_resumed: threading.Event | None = None
        self._allow_terminated_return: threading.Event | None = None

    def fail_on(self, subcommand: str) -> None:
        """Make the next and future invocations of `ob <subcommand> ...` exit non-zero."""
        self._failing_subcommands.add(subcommand)

    def clear_failures(self) -> None:
        """Stop future invocations from exiting non-zero, simulating a fixed vault for a retry."""
        self._failing_subcommands.clear()

    def raise_on(self, subcommand: str, *, continuous: bool = False) -> None:
        """Make matching invocations raise `FileNotFoundError` instead of returning a process.

        Models `subprocess.Popen` failing to exec (missing `ob` binary): the
        supervisor never gets a process object back. `continuous=True` targets
        the long-running `ob sync --continuous` child instead of the one-shots.
        """
        self._raising_targets.add((subcommand, continuous))

    def clear_raises(self) -> None:
        """Stop future invocations from raising, so a retry can run to completion."""
        self._raising_targets.clear()

    def hang_on(self, subcommand: str) -> None:
        """Make the next and future one-shot invocations of `ob <subcommand> ...` time out."""
        self._hanging_subcommands.add(subcommand)

    def clear_hangs(self) -> None:
        """Stop future invocations from hanging, simulating a stall clearing up for a retry."""
        self._hanging_subcommands.clear()

    def block_on(
        self, subcommand: str, *, pause_terminated_return: bool = False
    ) -> tuple[threading.Event, threading.Event]:
        """Make the next one-shot `ob <subcommand> ...` park inside `wait()` until released.

        Returns `(entered_wait, release_wait)`: `entered_wait` is set once that
        child is actually inside `wait()`, and the test must set `release_wait`
        to let it finish. Only the next matching invocation blocks, so a retry
        issued after the release runs to completion normally. When
        `pause_terminated_return` is true, tests may use the factory's return
        gates to schedule a newer attempt after termination woke the wait.
        """
        self._blocking_subcommand = subcommand
        self._entered_wait = threading.Event()
        self._release_wait = threading.Event()
        self._terminated_wait_resumed = threading.Event() if pause_terminated_return else None
        self._allow_terminated_return = threading.Event() if pause_terminated_return else None
        return self._entered_wait, self._release_wait

    def terminated_return_gates(self) -> tuple[threading.Event, threading.Event]:
        """Return gates configured by block_on(pause_terminated_return=True)."""
        assert self._terminated_wait_resumed is not None
        assert self._allow_terminated_return is not None
        return self._terminated_wait_resumed, self._allow_terminated_return

    def __call__(self, args: list[str], *, env: dict[str, str] | None = None) -> _FakeProcess:
        self.calls.append(args)
        is_continuous = '--continuous' in args
        subcommand = args[1] if len(args) > 1 else ''
        if (subcommand, is_continuous) in self._raising_targets:
            # The invocation is recorded first: a real Popen failure also means
            # the supervisor did attempt to start the child.
            raise FileNotFoundError(f'{args[0]}: no such executable')
        exit_code = 1 if (not is_continuous and subcommand in self._failing_subcommands) else 0
        hangs = not is_continuous and subcommand in self._hanging_subcommands
        entered_wait: threading.Event | None = None
        release_wait: threading.Event | None = None
        terminated_wait_resumed: threading.Event | None = None
        allow_terminated_return: threading.Event | None = None
        if not is_continuous and subcommand == self._blocking_subcommand:
            entered_wait, release_wait = self._entered_wait, self._release_wait
            terminated_wait_resumed = self._terminated_wait_resumed
            allow_terminated_return = self._allow_terminated_return
            self._blocking_subcommand = None
        process = _FakeProcess(
            args,
            env=env,
            exit_code=exit_code,
            hangs=hangs,
            entered_wait=entered_wait,
            release_wait=release_wait,
            terminated_wait_resumed=terminated_wait_resumed,
            allow_terminated_return=allow_terminated_return,
        )
        self.processes.append(process)
        return process


class TestObsidianHeadlessSupervisor(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.data_dir = Path(stack.enter_context(TemporaryDirectory()))
        self.factory = _FakePopenFactory()
        self.supervisor = ObsidianHeadlessSupervisor(self.data_dir, ob_executable='ob', popen_factory=self.factory)

    def test_ensure_vault_creates_working_tree(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue((self.data_dir / 'vaults' / 'Personal').is_dir())

    def test_ensure_vault_sanitizes_unsafe_vault_id_for_directory_name(self) -> None:
        self.supervisor.ensure_vault('My Vault/2', auth_token='tok', encryption_password=None)
        self.assertTrue((self.data_dir / 'vaults' / 'My_Vault_2').is_dir())
        self.assertFalse((self.data_dir / 'vaults' / 'My Vault').exists())

    def test_ensure_vault_runs_sync_setup_with_vault_and_path(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        setup_call = self.factory.calls[0]
        self.assertEqual(setup_call[0], 'ob')
        self.assertEqual(setup_call[1], 'sync-setup')
        self.assertIn('--vault', setup_call)
        self.assertEqual(setup_call[setup_call.index('--vault') + 1], 'Personal')
        self.assertIn('--path', setup_call)
        self.assertEqual(
            setup_call[setup_call.index('--path') + 1],
            str(self.data_dir / 'vaults' / 'Personal'),
        )

    def test_ensure_vault_passes_encryption_password_flag_when_given(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password='secret')
        setup_call = self.factory.calls[0]
        self.assertIn('--password', setup_call)
        self.assertEqual(setup_call[setup_call.index('--password') + 1], 'secret')

    def test_ensure_vault_omits_password_flag_when_not_encrypted(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        setup_call = self.factory.calls[0]
        self.assertNotIn('--password', setup_call)

    def test_ensure_vault_passes_auth_token_via_environment_not_args(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='super-secret-token', encryption_password=None)
        for call_args, process in zip(self.factory.calls, self.factory.processes):
            self.assertNotIn('super-secret-token', call_args)
            assert process.env is not None
            self.assertEqual(process.env['OBSIDIAN_AUTH_TOKEN'], 'super-secret-token')

    def test_ensure_vault_passes_minimal_env_not_full_process_environ(self) -> None:
        with unittest.mock.patch.dict(
            'os.environ', {'PATH': '/usr/bin', 'CREDENTIALS_KEY': 'backend-secret', 'POSTGRES_URL': 'postgres://x'}
        ):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)

        for process in self.factory.processes:
            assert process.env is not None
            self.assertNotIn('CREDENTIALS_KEY', process.env)
            self.assertNotIn('POSTGRES_URL', process.env)
            self.assertEqual(process.env['OBSIDIAN_AUTH_TOKEN'], 'tok')
            self.assertEqual(process.env.get('PATH'), '/usr/bin')

    def test_ensure_vault_runs_one_shot_sync_before_continuous(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        subcommands = [call[1] for call in self.factory.calls]
        self.assertEqual(subcommands, ['sync-setup', 'sync', 'sync'])
        one_shot_call, continuous_call = self.factory.calls[1], self.factory.calls[2]
        self.assertNotIn('--continuous', one_shot_call)
        self.assertIn('--continuous', continuous_call)

    def test_ensure_vault_marks_ready_after_successful_setup(self) -> None:
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))

    def test_ensure_vault_is_idempotent_and_does_not_restart(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        call_count_after_first = len(self.factory.calls)
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(len(self.factory.calls), call_count_after_first)

    def test_ensure_vault_raises_when_sync_setup_fails(self) -> None:
        self.factory.fail_on('sync-setup')
        with self.assertRaises(HeadlessSyncError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))

    def test_ensure_vault_raises_when_initial_sync_fails(self) -> None:
        self.factory.fail_on('sync')
        with self.assertRaises(HeadlessSyncError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(continuous_calls, [])

    def test_stop_vault_terminates_continuous_process(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        continuous_process = self.factory.processes[-1]
        self.supervisor.stop_vault('Personal')
        self.assertTrue(continuous_process.terminated)

    def test_stop_vault_clears_ready_marker(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.supervisor.stop_vault('Personal')
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))

    def test_stop_vault_leaves_working_tree_on_disk(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.supervisor.stop_vault('Personal')
        self.assertTrue((self.data_dir / 'vaults' / 'Personal').is_dir())

    def test_stop_vault_on_unknown_vault_does_not_raise(self) -> None:
        self.supervisor.stop_vault('Missing')

    def test_ensure_vault_can_restart_after_stop(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.supervisor.stop_vault('Personal')
        self.factory.calls.clear()
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertEqual(len(self.factory.calls), 3)

    def test_is_process_alive_true_after_ensure(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(self.supervisor.is_process_alive('Personal'))

    def test_is_process_alive_false_for_unknown_vault(self) -> None:
        self.assertFalse(self.supervisor.is_process_alive('Personal'))

    def test_is_process_alive_false_after_stop(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.supervisor.stop_vault('Personal')
        self.assertFalse(self.supervisor.is_process_alive('Personal'))

    def test_is_process_alive_false_when_continuous_child_exited(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        continuous_process = self.factory.processes[-1]
        continuous_process.mark_exited()
        self.assertFalse(self.supervisor.is_process_alive('Personal'))

    def test_ensure_vault_passes_configured_timeout_to_wait(self) -> None:
        supervisor = ObsidianHeadlessSupervisor(
            self.data_dir, ob_executable='ob', popen_factory=self.factory, one_shot_timeout_seconds=42.0
        )
        supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        one_shot_processes = self.factory.processes[:2]
        for process in one_shot_processes:
            self.assertEqual(process.wait_timeouts, [42.0])

    def test_ensure_vault_leaves_not_ready_when_sync_setup_times_out(self) -> None:
        self.factory.hang_on('sync-setup')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)

        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(self.supervisor.is_process_alive('Personal'))
        self.assertTrue(self.factory.processes[0].terminated)
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(continuous_calls, [])

    def test_ensure_vault_leaves_not_ready_when_initial_sync_times_out(self) -> None:
        self.factory.hang_on('sync')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)

        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertTrue(self.factory.processes[1].terminated)
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(continuous_calls, [])

    def test_ensure_vault_can_retry_after_a_timeout(self) -> None:
        self.factory.hang_on('sync-setup')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))

        self.factory.clear_hangs()
        self.factory.calls.clear()
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))

    def _ensure_in_thread(self, vault_id: str) -> tuple[threading.Thread, list[Exception]]:
        """Run `ensure_vault` for vault_id on a daemon thread, collecting any failure it raises.

        Returns the started thread plus the list that receives the raised
        failure, so callers can `join(timeout=...)` and assert on both liveness
        and outcome instead of deadlocking the suite on a locking regression.
        """
        raised: list[Exception] = []

        def run() -> None:
            try:
                self.supervisor.ensure_vault(vault_id, auth_token='tok', encryption_password=None)
            except Exception as failure:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                raised.append(failure)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, raised

    def test_unknown_vault_reports_not_started(self) -> None:
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)

    def test_successful_ensure_reports_ready(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_nonzero_setup_records_failed_state(self) -> None:
        self.factory.fail_on('sync-setup')
        with self.assertRaises(HeadlessSyncError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)

    def test_nonzero_initial_sync_records_failed_state(self) -> None:
        self.factory.fail_on('sync')
        with self.assertRaises(HeadlessSyncError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)

    def test_setup_timeout_records_partial_state(self) -> None:
        self.factory.hang_on('sync-setup')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

    def test_initial_sync_timeout_records_partial_state(self) -> None:
        self.factory.hang_on('sync')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

    def test_retry_after_timeout_reaches_ready_state(self) -> None:
        self.factory.hang_on('sync-setup')
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.PARTIAL)

        self.factory.clear_hangs()
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_stop_resets_sync_state(self) -> None:
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.supervisor.stop_vault('Personal')
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)

    def test_concurrent_ensure_during_one_shot_reuses_the_running_attempt(self) -> None:
        entered_wait, release_wait = self.factory.block_on('sync-setup')
        owner, owner_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_wait.wait(timeout=_EVENT_TIMEOUT_SECONDS))

        # The owner is parked in `ob sync-setup`; state must be observable and
        # the second caller must not be blocked behind the subprocess wait.
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.SYNCING)
        calls_during_attempt = len(self.factory.calls)

        concurrent, concurrent_failures = self._ensure_in_thread('Personal')
        concurrent.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(concurrent.is_alive())
        self.assertEqual(concurrent_failures, [])
        self.assertEqual(len(self.factory.calls), calls_during_attempt)

        release_wait.set()
        owner.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(owner.is_alive())
        self.assertEqual(owner_failures, [])
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))

    def test_stale_attempt_cannot_finalize_a_newer_attempt(self) -> None:
        # Attempt 1 parks inside `ob sync-setup`, is stopped, and only then
        # released — while attempt 2 is itself mid-sync. Attempt 1 must not
        # publish readiness (marker, continuous child, READY) that attempt 2
        # has not earned: `SYNCING` alone cannot identify the owning attempt.
        entered_first, release_first = self.factory.block_on('sync-setup')
        first, first_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_first.wait(timeout=_EVENT_TIMEOUT_SECONDS))

        self.supervisor.stop_vault('Personal')

        entered_second, release_second = self.factory.block_on('sync-setup')
        second, second_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_second.wait(timeout=_EVENT_TIMEOUT_SECONDS))
        calls_before_release = len(self.factory.calls)

        release_first.set()
        first.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_failures, [])

        # Attempt 2 still owns the vault and is still running, so nothing about
        # it may have changed, and the stale attempt may not run more `ob`.
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.SYNCING)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(self.supervisor.is_process_alive('Personal'))
        self.assertEqual(len(self.factory.calls), calls_before_release)

        release_second.set()
        second.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(second.is_alive())
        self.assertEqual(second_failures, [])
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(len(continuous_calls), 1)

    def test_stop_terminates_the_in_flight_one_shot(self) -> None:
        entered_wait, _release_wait = self.factory.block_on('sync-setup')
        owner, owner_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_wait.wait(timeout=_EVENT_TIMEOUT_SECONDS))
        blocked_process = self.factory.processes[0]
        self.assertFalse(blocked_process.terminated)

        self.supervisor.stop_vault('Personal')
        self.assertTrue(blocked_process.terminated)

        owner.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(owner.is_alive())
        self.assertEqual(owner_failures, [])
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(continuous_calls, [])

    def test_stopped_nonzero_one_shot_cannot_fail_or_alter_newer_attempt(self) -> None:
        """A terminated stale wait returns a signal code but cannot affect its successor."""
        entered_first, _release_first = self.factory.block_on('sync-setup', pause_terminated_return=True)
        wait_resumed, allow_first_return = self.factory.terminated_return_gates()
        first, first_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_first.wait(timeout=_EVENT_TIMEOUT_SECONDS))

        self.supervisor.stop_vault('Personal')
        self.assertTrue(wait_resumed.wait(timeout=_EVENT_TIMEOUT_SECONDS))

        entered_second, release_second = self.factory.block_on('sync-setup')
        second, second_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_second.wait(timeout=_EVENT_TIMEOUT_SECONDS))
        allow_first_return.set()
        first.join(timeout=_EVENT_TIMEOUT_SECONDS)

        self.assertFalse(first.is_alive())
        self.assertEqual(first_failures, [])
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.SYNCING)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))

        release_second.set()
        second.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(second.is_alive())
        self.assertEqual(second_failures, [])
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_one_shot_start_failure_records_failed_state(self) -> None:
        self.factory.raise_on('sync-setup')
        with self.assertRaises(FileNotFoundError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)

        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))

        self.factory.clear_raises()
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)

    def test_continuous_start_failure_leaves_vault_failed_without_marker(self) -> None:
        self.factory.raise_on('sync', continuous=True)
        with self.assertRaises(FileNotFoundError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)

        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(self.supervisor.is_process_alive('Personal'))

    def test_retry_after_failed_setup_reaches_ready_state(self) -> None:
        self.factory.fail_on('sync-setup')
        with self.assertRaises(HeadlessSyncError):
            self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.FAILED)

        self.factory.clear_failures()
        self.supervisor.ensure_vault('Personal', auth_token='tok', encryption_password=None)
        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.READY)
        self.assertTrue(self.supervisor.is_initial_sync_complete('Personal'))

    def test_stop_during_attempt_discards_stale_completion(self) -> None:
        entered_wait, release_wait = self.factory.block_on('sync')
        owner, owner_failures = self._ensure_in_thread('Personal')
        self.assertTrue(entered_wait.wait(timeout=_EVENT_TIMEOUT_SECONDS))

        self.supervisor.stop_vault('Personal')
        release_wait.set()
        owner.join(timeout=_EVENT_TIMEOUT_SECONDS)
        self.assertFalse(owner.is_alive())
        self.assertEqual(owner_failures, [])

        self.assertEqual(self.supervisor.sync_state('Personal'), VaultSyncState.NOT_STARTED)
        self.assertFalse(self.supervisor.is_initial_sync_complete('Personal'))
        self.assertFalse(self.supervisor.is_process_alive('Personal'))
        continuous_calls = [call for call in self.factory.calls if '--continuous' in call]
        self.assertEqual(continuous_calls, [])
