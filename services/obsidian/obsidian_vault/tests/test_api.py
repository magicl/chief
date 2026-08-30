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
from obsidian_vault.supervisor import FakeSupervisor, VaultSyncState

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
        """Wire the file service and app to the same supervisor instance."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.vault_root = Path(stack.enter_context(TemporaryDirectory()))
        self.store = VaultBindingStore()
        self.supervisor = FakeSupervisor()
        self.files = VaultFileService(
            self.store,
            vault_root_for=lambda vault_id: self.vault_root,
            sync_state_for=self.supervisor.sync_state,
        )
        self.client = self._make_client(self.supervisor)

    def _make_client(self, supervisor: FakeSupervisor) -> TestClient:
        """Build a client whose file-state callback matches its app supervisor."""
        self.files = VaultFileService(
            self.store,
            vault_root_for=lambda vault_id: self.vault_root,
            sync_state_for=supervisor.sync_state,
        )
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

    def test_partial_checkout_allows_reads_but_keeps_mutations_pending(self) -> None:
        """Pending ensure exposes seeded partial files while rejecting both mutations."""
        pending_supervisor = FakeSupervisor(auto_complete=False)
        client = self._make_client(pending_supervisor)
        response = client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(),
            headers=self._auth_headers(),
        )
        self.assertEqual(response.json(), {'ok': True, 'vaults': [{'vault_id': 'Personal', 'ready': False}]})

        journal = self.vault_root / 'Journal'
        journal.mkdir()
        (journal / 'note.md').write_text('partial', encoding='utf-8')
        list_response = client.get(
            '/v1/agents/agent-1/files',
            params={'vault_id': 'Personal', 'path': 'Journal'},
            headers=self._auth_headers(),
        )
        read_response = client.get(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            headers=self._auth_headers(),
        )
        self.assertEqual(list_response.json(), {'ok': True, 'entries': ['note.md']})
        self.assertEqual(read_response.json(), {'ok': True, 'content': 'partial'})

        write_response = client.put(
            '/v1/agents/agent-1/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'hello'},
            headers=self._auth_headers(),
        )
        append_response = client.post(
            '/v1/agents/agent-1/files/append',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'hello'},
            headers=self._auth_headers(),
        )
        self.assertEqual(write_response.status_code, 503)
        self.assertEqual(write_response.json()['error']['kind'], 'sync_pending')
        self.assertEqual(append_response.status_code, 503)
        self.assertEqual(append_response.json()['error']['kind'], 'sync_pending')

    def test_bound_not_started_checkout_returns_sync_pending_for_reads(self) -> None:
        """Bindings do not expose paths until the supervisor has started an attempt."""
        self.store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )

        for endpoint, path in (
            ('/v1/agents/agent-1/files', 'Journal'),
            ('/v1/agents/agent-1/files/content', 'Journal/note.md'),
        ):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(
                    endpoint,
                    params={'vault_id': 'Personal', 'path': path},
                    headers=self._auth_headers(),
                )
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['kind'], 'sync_pending')

        for method, endpoint in (
            ('put', '/v1/agents/agent-1/files/content'),
            ('post', '/v1/agents/agent-1/files/append'),
        ):
            with self.subTest(method=method):
                response = self.client.request(
                    method,
                    endpoint,
                    params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
                    json={'content': 'blocked'},
                    headers=self._auth_headers(),
                )
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['error']['kind'], 'sync_pending')

    def test_failed_checkout_returns_unavailable_for_every_file_operation(self) -> None:
        """A supervisor hard failure maps every file route to unavailable."""
        pending_supervisor = FakeSupervisor(auto_complete=False)
        client = self._make_client(pending_supervisor)
        response = client.put(
            '/v1/agents/agent-1/vaults',
            json=_ensure_body(),
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        pending_supervisor.fail('Personal')

        requests = (
            ('get', '/v1/agents/agent-1/files', {'path': 'Journal'}, None),
            ('get', '/v1/agents/agent-1/files/content', {'path': 'Journal/note.md'}, None),
            ('put', '/v1/agents/agent-1/files/content', {'path': 'Journal/note.md'}, {'content': 'write'}),
            ('post', '/v1/agents/agent-1/files/append', {'path': 'Journal/note.md'}, {'content': 'append'}),
        )
        for method, endpoint, params, body in requests:
            with self.subTest(method=method, endpoint=endpoint):
                response = client.request(
                    method,
                    endpoint,
                    params={'vault_id': 'Personal', **params},
                    json=body,
                    headers=self._auth_headers(),
                )
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()['error']['kind'], 'unavailable')

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


class _StatusTeardownRaceStore(VaultBindingStore):
    """Expose deterministic gates around last-ref teardown and status locking."""

    def __init__(self) -> None:
        """Initialize teardown observations while preserving normal store behavior."""
        super().__init__()
        self.release_agent_completed = threading.Event()
        self.release_lock_attempted = threading.Event()
        self.release_lock_finished = threading.Event()
        self._role_guard = threading.Lock()
        self._teardown_started = False
        self._next_lock_is_release = False

    def release_agent(self, agent_id: str) -> list[str]:
        """Signal after the last reference is removed but before route-level stop."""
        released = super().release_agent(agent_id)
        with self._role_guard:
            self._teardown_started = True
            self._next_lock_is_release = True
        self.release_agent_completed.set()
        return released

    def lock_for(self, vault_id: str) -> threading.Lock:
        """Wrap the shared vault lock with deterministic release/status ordering."""
        with self._role_guard:
            if not self._teardown_started:
                role = 'normal'
            elif self._next_lock_is_release:
                role = 'release'
                self._next_lock_is_release = False
            else:
                role = 'status'
        return cast(
            threading.Lock,
            _StatusTeardownRaceLock(
                super().lock_for(vault_id),
                self.release_lock_attempted,
                self.release_lock_finished,
                role,
            ),
        )

    def raw_lock_for(self, vault_id: str) -> threading.Lock:
        """Return the unwrapped vault lock so a test can pause route teardown."""
        return super().lock_for(vault_id)


class _StatusTeardownRaceLock:
    """Order a status lock after an already-waiting release route lock."""

    def __init__(
        self,
        inner: threading.Lock,
        release_lock_attempted: threading.Event,
        release_lock_finished: threading.Event,
        role: str,
    ) -> None:
        """Store the shared lock and events used to coordinate route threads."""
        self._inner = inner
        self._release_lock_attempted = release_lock_attempted
        self._release_lock_finished = release_lock_finished
        self._role = role

    def __enter__(self) -> threading.Lock:
        """Acquire in route order, forcing status to wait for teardown completion."""
        if self._role == 'release':
            self._release_lock_attempted.set()
        elif self._role == 'status':
            self._release_lock_finished.wait(timeout=5)
        self._inner.acquire()
        return self._inner

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Release the shared lock and signal completed teardown when applicable."""
        self._inner.release()
        if self._role == 'release':
            self._release_lock_finished.set()


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


class _StatusProbeSupervisor(FakeSupervisor):
    """Pause a status marker observation before the route publishes readiness."""

    def __init__(self) -> None:
        """Initialize a ready-capable fake with disabled status probing."""
        super().__init__()
        self.probe_enabled = False
        self.status_check_started = threading.Event()
        self.allow_status_check = threading.Event()

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Pause the selected marker read so teardown remains between remove and stop."""
        if self.probe_enabled:
            self.status_check_started.set()
            if not self.allow_status_check.wait(timeout=5):
                raise AssertionError('status marker check was not released')
        return super().is_initial_sync_complete(vault_id)


class _SingleFlightBlockingSupervisor(FakeSupervisor):
    """Model a supervisor that owns single-flight while its first ensure blocks."""

    def __init__(self) -> None:
        """Initialize gates and a counter for the one claimed attempt."""
        super().__init__(auto_complete=False)
        self._claim_guard = threading.Lock()
        self._claimed = False
        self.attempt_calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Claim one attempt and let duplicate callers return without waiting."""
        with self._claim_guard:
            if self._claimed:
                return
            self._claimed = True
            self.attempt_calls += 1
        self.started.set()
        if not self.release.wait(timeout=1):
            raise AssertionError('single-flight test did not release blocked ensure')
        super().ensure_vault(vault_id, auth_token=auth_token, encryption_password=encryption_password)

    def sync_state(self, vault_id: str) -> VaultSyncState:
        """Report SYNCING while the claimed attempt is blocked, like production."""
        if self.started.is_set() and not self.release.is_set():
            return VaultSyncState.SYNCING
        return super().sync_state(vault_id)


class _PublicationRaceSupervisor:
    """Expose controlled attempts whose completion can race last-ref release."""

    def __init__(self) -> None:
        """Initialize two deterministic attempt gates and guarded lifecycle state."""
        self._guard = threading.Lock()
        self._state = VaultSyncState.NOT_STARTED
        self._complete = False
        self._attempt_count = 0
        self.started = [threading.Event(), threading.Event()]
        self.release = [threading.Event(), threading.Event()]
        self.stop_calls = 0

    def ensure_vault(self, vault_id: str, *, auth_token: str, encryption_password: str | None = None) -> None:
        """Block each attempt, then publish completion independently of a racing stop."""
        del vault_id, auth_token, encryption_password
        with self._guard:
            attempt = self._attempt_count
            self._attempt_count += 1
            self._state = VaultSyncState.SYNCING
            self._complete = False
        self.started[attempt].set()
        if not self.release[attempt].wait(timeout=1):
            raise AssertionError(f'publication-race test did not release attempt {attempt}')
        with self._guard:
            # Deliberately adversarial: completion becomes visible after a
            # concurrent stop so the caller must recheck live references.
            self._state = VaultSyncState.READY
            self._complete = True

    def stop_vault(self, vault_id: str) -> None:
        """Record stop and clear the currently visible lifecycle state."""
        del vault_id
        with self._guard:
            self.stop_calls += 1
            self._state = VaultSyncState.NOT_STARTED
            self._complete = False

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return the guarded completion flag for the controlled attempt."""
        del vault_id
        with self._guard:
            return self._complete

    def sync_state(self, vault_id: str) -> VaultSyncState:
        """Return the guarded controlled sync state."""
        del vault_id
        with self._guard:
            return self._state

    def is_process_alive(self, vault_id: str) -> bool:
        """Return whether the controlled supervisor currently reports ready."""
        return self.sync_state(vault_id) == VaultSyncState.READY


class TestVaultApiSupervisorLocking(unittest.TestCase):
    """Check that app ensures defer single-flight ownership to the supervisor."""

    def test_concurrent_ensures_do_not_hold_store_lock_during_supervisor_wait(self) -> None:
        """Owner ensure wait leaves the store lock free; list/read succeed while SYNCING."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = VaultBindingStore()
        supervisor = _SingleFlightBlockingSupervisor()
        files = VaultFileService(
            store,
            vault_root_for=lambda vault_id: vault_root,
            sync_state_for=supervisor.sync_state,
        )
        app = create_app(token=TOKEN, store=store, files=files, supervisor=supervisor)
        ensure_client = TestClient(app)
        read_client = TestClient(app)

        def ensure_once(agent_id: str) -> None:
            """Issue one ensure request for a distinct agent sharing the vault."""
            ensure_client.put(
                f'/v1/agents/{agent_id}/vaults',
                json=_ensure_body(),
                headers={'Authorization': f'Bearer {TOKEN}'},
            )

        owner = threading.Thread(target=ensure_once, args=('agent-read',))
        owner.start()
        self.assertTrue(supervisor.started.wait(timeout=5))
        # Owner is blocked inside ensure_vault; that wait must not hold the
        # store file lock. Duplicate ensures later take only the short
        # publication lock, so do not sample locked() after they start.
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.SYNCING)
        self.assertFalse(store.lock_for('Personal').locked())

        journal = vault_root / 'Journal'
        journal.mkdir()
        (journal / 'note.md').write_text('syncing', encoding='utf-8')
        # Hold the file lock while listing/reading: if those routes waited on
        # store.lock_for they would deadlock on this non-reentrant lock.
        with store.lock_for('Personal'):
            self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.SYNCING)
            list_response = read_client.get(
                '/v1/agents/agent-read/files',
                params={'vault_id': 'Personal', 'path': 'Journal'},
                headers={'Authorization': f'Bearer {TOKEN}'},
            )
            read_response = read_client.get(
                '/v1/agents/agent-read/files/content',
                params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
                headers={'Authorization': f'Bearer {TOKEN}'},
            )
        self.assertEqual(list_response.json(), {'ok': True, 'entries': ['note.md']})
        self.assertEqual(read_response.json(), {'ok': True, 'content': 'syncing'})
        self.assertEqual(supervisor.sync_state('Personal'), VaultSyncState.SYNCING)

        duplicate_threads = [threading.Thread(target=ensure_once, args=(f'agent-{i}',)) for i in range(4)]
        for thread in duplicate_threads:
            thread.start()
        supervisor.release.set()
        threads = [owner, *duplicate_threads]
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(supervisor.attempt_calls, 1)
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_last_ref_release_prevents_stale_ready_publication(self) -> None:
        """A stopped attempt cannot republish ready; a new live attempt can."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = VaultBindingStore()
        supervisor = _PublicationRaceSupervisor()
        files = VaultFileService(
            store,
            vault_root_for=lambda vault_id: vault_root,
            sync_state_for=supervisor.sync_state,
        )
        app = create_app(token=TOKEN, store=store, files=files, supervisor=supervisor)
        ensure_client = TestClient(app, raise_server_exceptions=False)
        client = TestClient(app, raise_server_exceptions=False)
        headers = {'Authorization': f'Bearer {TOKEN}'}

        first_response: list[Any] = []

        def ensure_first() -> None:
            """Run the first ensure while the test releases its last reference."""
            first_response.append(ensure_client.put('/v1/agents/agent-1/vaults', json=_ensure_body(), headers=headers))

        first_thread = threading.Thread(target=ensure_first)
        first_thread.start()
        self.assertTrue(supervisor.started[0].wait(timeout=5))
        release_response = client.delete('/v1/agents/agent-1/vaults', headers=headers)
        self.assertEqual(release_response.status_code, 200)
        supervisor.release[0].set()
        first_thread.join(timeout=5)

        self.assertFalse(first_thread.is_alive())
        self.assertEqual(supervisor.stop_calls, 1)
        self.assertFalse(store.is_vault_ready('Personal'))

        second_response: list[Any] = []

        def ensure_second() -> None:
            """Run a fresh referenced attempt that may legitimately publish ready."""
            second_response.append(ensure_client.put('/v1/agents/agent-2/vaults', json=_ensure_body(), headers=headers))

        second_thread = threading.Thread(target=ensure_second)
        second_thread.start()
        self.assertTrue(supervisor.started[1].wait(timeout=5))
        pending_write = client.put(
            '/v1/agents/agent-2/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'blocked'},
            headers=headers,
        )
        self.assertEqual(pending_write.status_code, 503)
        supervisor.release[1].set()
        second_thread.join(timeout=5)

        self.assertFalse(second_thread.is_alive())
        self.assertEqual(second_response[0].status_code, 200)
        ready_write = client.put(
            '/v1/agents/agent-2/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'ready'},
            headers=headers,
        )
        self.assertEqual(ready_write.status_code, 200)

    def test_status_during_last_ref_teardown_cannot_republish_ready(self) -> None:
        """Status waits for teardown and leaves a later binding gated until live completion."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = _StatusTeardownRaceStore()
        supervisor = _StatusProbeSupervisor()
        files = VaultFileService(
            store,
            vault_root_for=lambda vault_id: vault_root,
            sync_state_for=supervisor.sync_state,
        )
        app = create_app(token=TOKEN, store=store, files=files, supervisor=supervisor)
        client = TestClient(app)
        headers = {'Authorization': f'Bearer {TOKEN}'}
        self.assertEqual(
            client.put('/v1/agents/agent-1/vaults', json=_ensure_body(), headers=headers).status_code,
            200,
        )

        teardown_pause = store.raw_lock_for('Personal')
        teardown_pause.acquire()
        responses: dict[str, Any] = {}

        def release_last_reference() -> None:
            """Issue the last-reference release while its route lock is paused."""
            responses['release'] = client.delete('/v1/agents/agent-1/vaults', headers=headers)

        release_thread = threading.Thread(target=release_last_reference, name='release-route')
        release_thread.start()
        self.assertTrue(store.release_agent_completed.wait(timeout=5))
        self.assertTrue(store.release_lock_attempted.wait(timeout=5))
        self.assertFalse(store.is_vault_ready('Personal'))
        supervisor.probe_enabled = True

        def poll_status() -> None:
            """Poll status while teardown has removed refs but not stopped sync."""
            responses['status'] = client.get('/v1/vaults/Personal/status', headers=headers)

        status_thread = threading.Thread(target=poll_status, name='status-route')
        status_thread.start()
        self.assertTrue(supervisor.status_check_started.wait(timeout=5))
        supervisor.allow_status_check.set()
        teardown_pause.release()
        release_thread.join(timeout=5)
        status_thread.join(timeout=5)

        self.assertFalse(release_thread.is_alive())
        self.assertFalse(status_thread.is_alive())
        self.assertEqual(responses['status'].json()['ready'], False)
        self.assertFalse(store.is_vault_ready('Personal'))

        pending_supervisor = FakeSupervisor(auto_complete=False)
        pending_files = VaultFileService(
            store,
            vault_root_for=lambda vault_id: vault_root,
            sync_state_for=pending_supervisor.sync_state,
        )
        pending_client = TestClient(
            create_app(token=TOKEN, store=store, files=pending_files, supervisor=pending_supervisor)
        )
        self.assertEqual(
            pending_client.put('/v1/agents/agent-2/vaults', json=_ensure_body(), headers=headers).status_code,
            200,
        )
        blocked_write = pending_client.put(
            '/v1/agents/agent-2/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'blocked'},
            headers=headers,
        )
        self.assertEqual(blocked_write.status_code, 503)
        pending_supervisor.complete('Personal')
        self.assertEqual(
            pending_client.get('/v1/vaults/Personal/status', headers=headers).json()['ready'],
            True,
        )
        live_write = pending_client.put(
            '/v1/agents/agent-2/files/content',
            params={'vault_id': 'Personal', 'path': 'Journal/note.md'},
            json={'content': 'ready'},
            headers=headers,
        )
        self.assertEqual(live_write.status_code, 200)


class TestVaultApiReleaseStopRace(unittest.TestCase):
    """Regression: skip stop_vault when another agent re-binds before teardown runs."""

    def test_release_skips_stop_when_vault_reacquired_before_lock(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        vault_root = Path(stack.enter_context(TemporaryDirectory()))
        store = _GatedVaultLockStore()
        supervisor = _StopCallTrackingSupervisor()
        files = VaultFileService(
            store,
            vault_root_for=lambda vault_id: vault_root,
            sync_state_for=supervisor.sync_state,
        )
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
