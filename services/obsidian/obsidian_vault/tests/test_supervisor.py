# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import contextlib
import subprocess
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from obsidian_vault.supervisor import (
    FakeSupervisor,
    HeadlessSyncError,
    ObsidianHeadlessSupervisor,
)


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


class _FakeProcess:
    """Stand-in for `subprocess.Popen` that records calls and lets tests script exit codes."""

    def __init__(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        exit_code: int = 0,
        hangs: bool = False,
    ) -> None:
        self.args = args
        self.env = env
        self._exit_code = exit_code
        self._hangs = hangs
        self._alive = True
        self.terminated = False
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        """Return the scripted exit code, or raise TimeoutExpired if scripted to hang."""
        self.wait_timeouts.append(timeout)
        if self._hangs:
            raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout or 0)
        self._alive = False
        return self._exit_code

    def terminate(self) -> None:
        """Record that this process was asked to terminate."""
        self.terminated = True
        self._alive = False

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
    instead, to exercise the timeout path.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.processes: list[_FakeProcess] = []
        self._failing_subcommands: set[str] = set()
        self._hanging_subcommands: set[str] = set()

    def fail_on(self, subcommand: str) -> None:
        """Make the next and future invocations of `ob <subcommand> ...` exit non-zero."""
        self._failing_subcommands.add(subcommand)

    def hang_on(self, subcommand: str) -> None:
        """Make the next and future one-shot invocations of `ob <subcommand> ...` time out."""
        self._hanging_subcommands.add(subcommand)

    def clear_hangs(self) -> None:
        """Stop future invocations from hanging, simulating a stall clearing up for a retry."""
        self._hanging_subcommands.clear()

    def __call__(self, args: list[str], *, env: dict[str, str] | None = None) -> _FakeProcess:
        self.calls.append(args)
        is_continuous = '--continuous' in args
        subcommand = args[1] if len(args) > 1 else ''
        exit_code = 1 if (not is_continuous and subcommand in self._failing_subcommands) else 0
        hangs = not is_continuous and subcommand in self._hanging_subcommands
        process = _FakeProcess(args, env=env, exit_code=exit_code, hangs=hangs)
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
