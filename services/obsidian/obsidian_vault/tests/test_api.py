# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import contextlib
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any, cast

from fastapi.testclient import TestClient
from obsidian_vault.app import create_app
from obsidian_vault.bindings import VaultBindingStore
from obsidian_vault.files import VaultFileService
from obsidian_vault.supervisor import FakeSupervisor

TOKEN = 'test-service-token'


def _ensure_body(vault_id: str = 'Personal', roots: list[str] | None = None) -> dict[str, Any]:
    return {
        'bindings': [
            {
                'vault_id': vault_id,
                'roots': roots if roots is not None else ['Journal'],
                'credential': {'auth_token': 'sync-tok', 'encryption_password': None},
            }
        ]
    }


class TestVaultApi(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.vault_root = Path(stack.enter_context(TemporaryDirectory()))
        self.store = VaultBindingStore()
        self.files = VaultFileService(self.store, vault_root_for=lambda vault_id: self.vault_root)
        self.supervisor = FakeSupervisor()
        self.client = self._make_client(self.supervisor)

    def _make_client(self, supervisor: FakeSupervisor) -> TestClient:
        app = create_app(token=TOKEN, store=self.store, files=self.files, supervisor=supervisor)
        return TestClient(app)

    def _auth_headers(self, token: str = TOKEN) -> dict[str, str]:
        return {'Authorization': f'Bearer {token}'}

    def _ensure(self, vault_id: str = 'Personal', roots: list[str] | None = None) -> None:
        response = self.client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(vault_id, roots),
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)

    def test_missing_bearer_header_is_unauthorized(self) -> None:
        response = self.client.get('/v1/vaults/Personal/status')
        self.assertEqual(response.status_code, 401)

    def test_wrong_bearer_token_is_unauthorized(self) -> None:
        response = self.client.get('/v1/vaults/Personal/status', headers=self._auth_headers('wrong'))
        self.assertEqual(response.status_code, 401)

    def test_ensure_with_auto_complete_supervisor_marks_vault_ready(self) -> None:
        response = self.client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(),
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body, {'ok': True, 'vaults': [{'vault_id': 'Personal', 'ready': True}]})

    def test_status_reflects_readiness_after_ensure(self) -> None:
        self._ensure()
        response = self.client.get('/v1/vaults/Personal/status', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'vault_id': 'Personal', 'ready': True, 'initial_sync_complete': True, 'sync_process_alive': True},
        )

    def test_status_for_unknown_vault_is_not_ready(self) -> None:
        response = self.client.get('/v1/vaults/Unknown/status', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'vault_id': 'Unknown', 'ready': False, 'initial_sync_complete': False, 'sync_process_alive': False},
        )

    def test_status_reports_sync_process_liveness_independent_of_readiness(self) -> None:
        self._ensure()
        self.supervisor.kill('Personal')
        response = self.client.get('/v1/vaults/Personal/status', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ready'])
        self.assertFalse(body['sync_process_alive'])

    def test_write_before_first_sync_completes_returns_sync_pending(self) -> None:
        pending_supervisor = FakeSupervisor(auto_complete=False)
        client = self._make_client(pending_supervisor)
        response = client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(),
            headers=self._auth_headers(),
        )
        self.assertEqual(response.json(), {'ok': True, 'vaults': [{'vault_id': 'Personal', 'ready': False}]})

        response = client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'hello'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['kind'], 'sync_pending')

        pending_supervisor.complete('Personal')
        status_response = client.get('/v1/vaults/Personal/status', headers=self._auth_headers())
        self.assertEqual(
            status_response.json(),
            {'vault_id': 'Personal', 'ready': True, 'initial_sync_complete': True, 'sync_process_alive': True},
        )

        response = client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'hello'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True})

    def test_write_then_read_roundtrip(self) -> None:
        self._ensure()
        write_response = self.client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'hello'},
            headers=self._auth_headers(),
        )
        self.assertEqual(write_response.status_code, 200)
        self.assertEqual(write_response.json(), {'ok': True})

        read_response = self.client.get(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), {'ok': True, 'content': 'hello'})

    def test_append_creates_file_then_read_sees_full_content(self) -> None:
        self._ensure()
        self.client.post(
            '/v1/agents/agent-1/files/append',
            params={'vault_id': 'Personal', 'path': 'Journal/log.md'},
            json={'content': 'line1\n'},
            headers=self._auth_headers(),
        )
        append_response = self.client.post(
            '/v1/agents/agent-1/files/append',
            params={'vault_id': 'Personal', 'path': 'Journal/log.md'},
            json={'content': 'line2\n'},
            headers=self._auth_headers(),
        )
        self.assertEqual(append_response.status_code, 200)

        read_response = self.client.get(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/log.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(read_response.json(), {'ok': True, 'content': 'line1\nline2\n'})

    def test_list_returns_sorted_entries(self) -> None:
        self._ensure()
        self.client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/b.md'},
            json={'content': 'b'},
            headers=self._auth_headers(),
        )
        self.client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/a.md'},
            json={'content': 'a'},
            headers=self._auth_headers(),
        )

        response = self.client.get(
            '/v1/agents/agent-1/files',
            params={'vault_id': 'Personal', 'path': 'Journal'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'entries': ['a.md', 'b.md']})

    def test_read_missing_file_returns_not_found(self) -> None:
        self._ensure()
        response = self.client.get(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/missing.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['kind'], 'not_found')

    def test_write_outside_configured_roots_returns_forbidden(self) -> None:
        self._ensure()
        response = self.client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Secrets/x.md'},
            json={'content': 'nope'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['kind'], 'outside_root')

    def test_file_op_for_unbound_agent_returns_unavailable(self) -> None:
        response = self.client.get(
            '/v1/agents/unknown-agent/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['kind'], 'unavailable')

    def test_release_stops_supervisor_and_revokes_access(self) -> None:
        self._ensure()
        self.assertTrue(self.supervisor.is_running('Personal'))

        response = self.client.delete('/v1/agents/agent-1/vaults', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'released': ['Personal']})
        self.assertFalse(self.supervisor.is_running('Personal'))

        response = self.client.get(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 500)

    def test_release_with_no_bindings_reports_none_released(self) -> None:
        response = self.client.delete('/v1/agents/never-bound/vaults', headers=self._auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'released': []})

    def test_empty_configured_token_never_authenticates(self) -> None:
        app = create_app(token='', store=self.store, files=self.files, supervisor=self.supervisor)
        client = TestClient(app)
        response = client.get('/v1/vaults/Personal/status', headers={'Authorization': 'Bearer '})
        self.assertEqual(response.status_code, 401)


class _GatedVaultLockStore(VaultBindingStore):
    """VaultBindingStore whose per-vault lock blocks until `open_vault_lock_gate` is set.

    Used to simulate a second agent re-acquiring a vault after `release_agent`
    returns it as refcount-zero but before `release_vaults` acquires the lock
    and calls `stop_vault`.
    """

    def __init__(self) -> None:
        super().__init__()
        self._vault_lock_gate = threading.Event()
        self.release_agent_completed = threading.Event()

    def lock_for(self, vault_id: str) -> threading.Lock:
        return cast(threading.Lock, _GatedVaultLock(super().lock_for(vault_id), self._vault_lock_gate))

    def release_agent(self, agent_id: str) -> list[str]:
        """Record completion so tests can re-acquire only after refcount hit zero."""
        released = super().release_agent(agent_id)
        self.release_agent_completed.set()
        return released

    def open_vault_lock_gate(self) -> None:
        """Allow threads blocked on the per-vault lock to proceed."""
        self._vault_lock_gate.set()


class _GatedVaultLock:
    """Context-manager wrapper that waits on a gate before acquiring the inner lock."""

    def __init__(self, inner: threading.Lock, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate

    def __enter__(self) -> threading.Lock:
        self._gate.wait(timeout=5)
        self._inner.acquire()
        return self._inner

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._inner.release()


class _StopCallTrackingSupervisor(FakeSupervisor):
    """FakeSupervisor that records every stop_vault invocation."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_calls: list[str] = []

    def stop_vault(self, vault_id: str) -> None:
        self.stop_calls.append(vault_id)
        super().stop_vault(vault_id)


class _ConcurrencyTrackingSupervisor(FakeSupervisor):
    """FakeSupervisor that records the max number of concurrent ensure_vault calls.

    `hold_seconds` simulates the real supervisor's blocking `ob` one-shot
    calls, giving a second concurrent request a window to race in if the
    caller isn't serializing ensure calls per vault.
    """

    def __init__(self, *, hold_seconds: float = 0.05) -> None:
        super().__init__()
        self._active = 0
        self.max_concurrent = 0
        self._guard = threading.Lock()
        self._hold_seconds = hold_seconds

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        with self._guard:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        time.sleep(self._hold_seconds)
        super().ensure_vault(vault_id, auth_token=auth_token, encryption_password=encryption_password)
        with self._guard:
            self._active -= 1


class TestVaultApiSupervisorLocking(unittest.TestCase):
    """Check that concurrent ensure requests for one vault can't race the supervisor."""

    def test_concurrent_ensure_calls_for_same_vault_are_serialized(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = VaultBindingStore()
        files = VaultFileService(store, vault_root_for=lambda vault_id: vault_root)
        supervisor = _ConcurrencyTrackingSupervisor(hold_seconds=0.05)
        app = create_app(token=TOKEN, store=store, files=files, supervisor=supervisor)
        client = TestClient(app)

        def ensure_once(agent_id: str) -> None:
            client.put(
                f'/v1/agents/{agent_id}/vaults',
                json=_ensure_body(),
                headers={'Authorization': f'Bearer {TOKEN}'},
            )

        threads = [threading.Thread(target=ensure_once, args=(f'agent-{i}',)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(supervisor.max_concurrent, 1)


class TestVaultApiReleaseStopRace(unittest.TestCase):
    """Regression: skip stop_vault when another agent re-binds before teardown runs."""

    def test_release_skips_stop_when_vault_reacquired_before_lock(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = _GatedVaultLockStore()
        files = VaultFileService(store, vault_root_for=lambda vault_id: vault_root)
        supervisor = _StopCallTrackingSupervisor()
        app = create_app(token=TOKEN, store=store, files=files, supervisor=supervisor)
        client = TestClient(app)

        response = client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(),
            headers={'Authorization': f'Bearer {TOKEN}'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(supervisor.is_running('Personal'))

        release_error: list[BaseException] = []
        release_done = threading.Event()
        reacquire_error: list[BaseException] = []
        reacquire_done = threading.Event()

        def release_agent_one() -> None:
            try:
                response = client.delete('/v1/agents/agent-1/vaults', headers={'Authorization': f'Bearer {TOKEN}'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'ok': True, 'released': ['Personal']})
            except BaseException as exc:  # noqa: BLE001
                release_error.append(exc)
            finally:
                release_done.set()

        def reacquire_for_agent_two() -> None:
            try:
                response = client.put(
                    '/v1/agents/agent-2/vaults',
                    json=_ensure_body(),
                    headers={'Authorization': f'Bearer {TOKEN}'},
                )
                self.assertEqual(response.status_code, 200)
            except BaseException as exc:  # noqa: BLE001
                reacquire_error.append(exc)
            finally:
                reacquire_done.set()

        release_thread = threading.Thread(target=release_agent_one)
        release_thread.start()
        self.assertTrue(store.release_agent_completed.wait(timeout=5))

        reacquire_thread = threading.Thread(target=reacquire_for_agent_two)
        reacquire_thread.start()

        time.sleep(0.05)
        store.open_vault_lock_gate()

        release_thread.join(timeout=5)
        reacquire_thread.join(timeout=5)
        self.assertFalse(release_error, release_error)
        self.assertFalse(reacquire_error, reacquire_error)
        self.assertTrue(release_done.is_set())
        self.assertTrue(reacquire_done.is_set())

        self.assertEqual(supervisor.stop_calls, [])
        self.assertTrue(supervisor.is_running('Personal'))
