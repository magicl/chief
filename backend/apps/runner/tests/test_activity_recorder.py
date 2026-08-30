# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Contract tests for Django-free and backend-backed activity recorders."""

import inspect
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier, Thread
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from apps.runner.activity_recorder import BackendActivityRecorder
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.backends.memory import MemorySessionBackend
from apps.sessions.models import AgentSessionActivity, AgentSessionActivityStatus
from apps.sessions.services import commands as session_commands
from apps.sessions.tests.base import make_test_session
from django.core.exceptions import ValidationError
from django.db import transaction
from libs.agent_spec import load_example
from libs.tools import NoOpActivityRecorder, ToolContext

from olib.py.django.test.cases import OTestCase, OTransactionTestCase


class TestNoOpActivityRecorder(OTestCase):
    """Verify offline tool recording remains safe and synthetic."""

    def test_synthetic_refs_and_contexts_work(self) -> None:
        """No-op lifecycle methods return stable synthetic handles."""
        recorder = NoOpActivityRecorder()

        started = recorder.start(kind='tool', name='clock', summary='Clock')
        completed = recorder.complete(started.id, summary='Done')
        failed = recorder.fail(started.id, summary='Failed')
        note = recorder.status_note(name='ready', summary='Ready')
        with recorder.push_parent(started.id), recorder.span(name='nested') as span:
            pass

        self.assertEqual(completed.id, started.id)
        self.assertEqual(completed.status, 'succeeded')
        self.assertEqual(failed.id, started.id)
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(note.kind, 'status')
        self.assertEqual(note.status, 'succeeded')
        self.assertEqual(span.kind, 'span')

    def test_link_subagent_requires_session_recorder(self) -> None:
        """Offline recorders refuse operations that require a session."""
        with self.assertRaises(RuntimeError):
            NoOpActivityRecorder().link_subagent(
                agent_id=uuid4(),
                name='child',
                summary='Delegate',
            )

    def test_complete_rejects_nonterminal_status(self) -> None:
        """No-op completion rejects lifecycle statuses that are not terminal."""
        recorder = NoOpActivityRecorder()
        started = recorder.start(kind='tool', name='clock', summary='Calling')

        with self.assertRaisesRegex(ValueError, 'complete status must be terminal'):
            recorder.complete(started.id, summary='Still running', status='running')


class TestToolContextRecorderDefault(OTestCase):
    """Tool contexts remain source-compatible outside runner sessions."""

    def test_default_recorder_is_noop(self) -> None:
        """A context built without runner wiring receives the no-op recorder."""
        context = ToolContext(spec=load_example('clock-assistant'), user_id=1)

        self.assertIsInstance(context.recorder, NoOpActivityRecorder)


class TestMemoryActivityRecorder(OTestCase):
    """Exercise scoped recording without Django persistence."""

    def _recorder(self) -> tuple[MemorySessionBackend, BackendActivityRecorder]:
        """Build a fresh memory backend and its recorder."""
        backend = MemorySessionBackend(load_example('clock-assistant'), user_id=1)
        return backend, BackendActivityRecorder(backend)

    def test_nested_tool_span_revisions_and_statuses(self) -> None:
        """A nested span inherits its tool parent and both become terminal."""
        backend, recorder = self._recorder()

        tool = recorder.start(kind='tool', name='clock__now', summary='Calling clock')
        with recorder.push_parent(tool.id):
            with recorder.span(name='serialize', summary='Serialize result') as span:
                recorder.status_note(name='checkpoint', summary='Serialized')
        completed = recorder.complete(tool.id, summary='Clock complete', details={'result': 'ok'})

        activities = backend.activities()
        self.assertEqual([activity.seq for activity in activities], [1, 2, 3])
        self.assertEqual(activities[1].parent_id, tool.id)
        self.assertEqual(activities[2].parent_id, span.id)
        self.assertEqual(activities[1].status, 'succeeded')
        self.assertEqual(activities[1].revision, 2)
        self.assertEqual(completed.status, 'succeeded')
        self.assertEqual(completed.revision, 2)
        self.assertEqual(len(backend.published_activities), 5)

    def test_span_failure_restores_parent_stack(self) -> None:
        """A raised span fails and later work uses the enclosing parent."""
        backend, recorder = self._recorder()
        outer = recorder.start(kind='tool', name='outer', summary='Outer')

        with recorder.push_parent(outer.id):
            with self.assertRaisesRegex(ValueError, 'boom'):
                with recorder.span(name='inner'):
                    raise ValueError('boom')
            note = recorder.status_note(name='recovered', summary='Recovered')

        span = backend.activities()[1]
        recorded_note = next(activity for activity in backend.activities() if activity.id == note.id)
        self.assertEqual(span.status, 'failed')
        self.assertEqual(span.revision, 2)
        self.assertEqual(recorded_note.parent_id, outer.id)

    def test_nested_push_parent_always_restores_previous_scope(self) -> None:
        """Nested parent overrides unwind to the prior scope and then root."""
        backend, recorder = self._recorder()
        first = recorder.start(kind='span', name='first', summary='First')
        second = recorder.start(kind='span', name='second', summary='Second')

        with recorder.push_parent(first.id):
            with recorder.push_parent(second.id):
                nested = recorder.status_note(name='nested', summary='Nested')
            restored = recorder.status_note(name='restored', summary='Restored')
        root = recorder.status_note(name='root', summary='Root')

        by_id = {activity.id: activity for activity in backend.activities()}
        self.assertEqual(by_id[nested.id].parent_id, second.id)
        self.assertEqual(by_id[restored.id].parent_id, first.id)
        self.assertIsNone(by_id[root.id].parent_id)
        self.assertEqual(len(by_id), 5)

    def test_link_subagent_creates_reference_only_memory_activity(self) -> None:
        """Memory recording simulates a child identity without dispatching a run."""
        backend, recorder = self._recorder()

        reference = recorder.link_subagent(
            agent_id=uuid4(),
            name='child',
            summary='Delegate',
        )

        activity = next(activity for activity in backend.activities() if activity.id == reference.id)
        self.assertEqual(activity.kind, 'subagent')
        self.assertIsNotNone(activity.child_session_id)
        self.assertTrue(activity.details['memory_link_only'])

    def test_create_return_cannot_mutate_nested_backend_details(self) -> None:
        """Input and returned snapshot mutations cannot change stored details."""
        backend, _ = self._recorder()
        details = {'request': {'query': 'original'}}

        created = backend.create_activity(
            kind='span',
            status='running',
            name='mutable',
            summary='Mutable',
            details=details,
        )
        details['request']['query'] = 'input changed'
        created.details['request']['query'] = 'return changed'

        self.assertEqual(
            backend.activities()[0].details,
            {'request': {'query': 'original'}},
        )

    def test_activities_return_cannot_mutate_nested_backend_details(self) -> None:
        """Each activities query returns detached nested detail values."""
        backend, recorder = self._recorder()
        recorder.start(
            kind='span',
            name='detached',
            summary='Detached',
            details={'request': {'query': 'original'}},
        )

        returned = backend.activities()
        returned[0].details['request']['query'] = 'changed'

        self.assertEqual(
            backend.activities()[0].details,
            {'request': {'query': 'original'}},
        )

    def test_published_return_cannot_mutate_historical_payload(self) -> None:
        """Published payload access returns detached nested detail values."""
        backend, recorder = self._recorder()
        recorder.start(
            kind='span',
            name='published',
            summary='Published',
            details={'request': {'query': 'original'}},
        )

        published = backend.published_activities
        published[0]['details']['request']['query'] = 'changed'

        self.assertEqual(
            backend.published_activities[0]['details'],
            {'request': {'query': 'original'}},
        )

    def test_terminal_activity_is_immutable(self) -> None:
        """Normal updates cannot revise a terminal memory activity."""
        backend, recorder = self._recorder()
        started = recorder.start(kind='tool', name='clock', summary='Calling')
        completed = recorder.complete(started.id, summary='Done')

        with self.assertRaisesRegex(ValueError, 'terminal activity is immutable'):
            backend.update_activity(started.id, status='failed', summary='Regressed')

        current = backend.activities()[0]
        self.assertEqual(current.status, 'succeeded')
        self.assertEqual(current.revision, completed.revision)

    def test_terminal_subagent_has_no_generic_reconcile_escape(self) -> None:
        """Memory updates keep terminal references immutable like Django updates."""
        backend, _ = self._recorder()
        subagent = backend.create_activity(
            kind='subagent',
            status='succeeded',
            name='child',
            summary='Finished',
            details={'safe': {'value': True}},
        )

        self.assertNotIn(
            'allow_terminal_reconcile',
            inspect.signature(backend.update_activity).parameters,
        )
        with self.assertRaisesRegex(ValueError, 'terminal activity is immutable'):
            backend.update_activity(subagent.id, status='failed')

    def test_memory_rejects_child_link_on_non_subagent(self) -> None:
        """Memory activity creation enforces the canonical child-link kind."""
        backend, _ = self._recorder()

        with self.assertRaisesRegex(ValueError, 'only on subagent'):
            backend.create_activity(
                kind='span',
                status='running',
                name='invalid',
                summary='Invalid',
                details={},
                child_session_id=uuid4(),
            )

    def test_complete_rejects_nonterminal_status(self) -> None:
        """Backend completion rejects nonterminal lifecycle statuses."""
        backend, recorder = self._recorder()
        started = recorder.start(kind='tool', name='clock', summary='Calling')

        with self.assertRaisesRegex(ValueError, 'complete status must be terminal'):
            recorder.complete(started.id, summary='Still running', status='running')

        self.assertEqual(backend.activities()[0].status, 'running')
        self.assertEqual(backend.activities()[0].revision, 1)

    def test_concurrent_creates_allocate_contiguous_unique_sequences(self) -> None:
        """Concurrent memory creates serialize sequence allocation."""
        backend, _ = self._recorder()
        worker_count = 16
        creates_per_worker = 20
        start = Barrier(worker_count)
        previous_interval = sys.getswitchinterval()

        def create_batch(worker: int) -> list[int]:
            """Create one synchronized batch and return allocated sequences."""
            start.wait()
            return [
                backend.create_activity(
                    kind='status',
                    status='succeeded',
                    name=f'worker-{worker}',
                    summary=f'item-{item}',
                    details={},
                ).seq
                for item in range(creates_per_worker)
            ]

        try:
            sys.setswitchinterval(1e-6)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                allocated = [seq for batch in executor.map(create_batch, range(worker_count)) for seq in batch]
        finally:
            sys.setswitchinterval(previous_interval)

        expected = list(range(1, worker_count * creates_per_worker + 1))
        self.assertEqual(sorted(allocated), expected)
        self.assertEqual([activity.seq for activity in backend.activities()], expected)

    def test_concurrent_updates_preserve_every_revision(self) -> None:
        """Concurrent memory updates serialize revision increments."""
        backend, _ = self._recorder()
        activity = backend.create_activity(
            kind='span',
            status='running',
            name='shared',
            summary='Shared',
            details={},
        )
        worker_count = 32
        start = Barrier(worker_count)

        def update_once(worker: int) -> int:
            """Apply one synchronized update and return its observed revision."""
            start.wait()
            return backend.update_activity(
                activity.id,
                summary=f'worker-{worker}',
            ).revision

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            revisions = list(executor.map(update_once, range(worker_count)))

        self.assertEqual(sorted(revisions), list(range(2, worker_count + 2)))
        self.assertEqual(backend.activities()[0].revision, worker_count + 1)

    def test_new_thread_has_independent_parent_scope(self) -> None:
        """A newly spawned thread does not inherit the caller's parent stack."""
        backend, recorder = self._recorder()
        parent = recorder.start(kind='span', name='parent', summary='Parent')
        created = []

        def record_in_thread() -> None:
            """Create one status note in a fresh thread context."""
            created.append(recorder.status_note(name='thread', summary='Thread'))

        with recorder.push_parent(parent.id):
            thread = Thread(target=record_in_thread)
            thread.start()
            thread.join()

        recorded = next(activity for activity in backend.activities() if activity.id == created[0].id)
        self.assertIsNone(recorded.parent_id)

    def test_copied_context_thread_preserves_parent_scope(self) -> None:
        """An explicitly copied context carries the current parent into a thread."""
        backend, recorder = self._recorder()
        parent = recorder.start(kind='span', name='parent', summary='Parent')
        created = []

        def record_in_thread() -> None:
            """Create one status note under the copied parent context."""
            created.append(recorder.status_note(name='thread', summary='Thread'))

        with recorder.push_parent(parent.id):
            context = copy_context()
            thread = Thread(target=context.run, args=(record_in_thread,))
            thread.start()
            thread.join()

        recorded = next(activity for activity in backend.activities() if activity.id == created[0].id)
        self.assertEqual(recorded.parent_id, parent.id)


class TestDjangoActivityRecorder(OTransactionTestCase):
    """Verify backend behavior delegates to canonical session services."""

    def test_memory_and_django_reject_invalid_update_status_without_revision(self) -> None:
        """Omitted status is distinct from every invalid non-null status."""
        session = make_test_session('update-status-parity')
        backends = [
            DjangoSessionBackend(session),
            MemorySessionBackend(load_example('clock-assistant'), user_id=session.agent.user_id),
        ]
        outcomes = []

        for backend in backends:
            with self.subTest(backend=type(backend).__name__):
                activity = backend.create_activity(
                    kind='span',
                    status='running',
                    name='work',
                    summary='original',
                    details={'step': 1},
                )
                for invalid_status in ('', 'not-a-status'):
                    with (
                        self.subTest(invalid_status=invalid_status),
                        self.assertRaises((ValueError, ValidationError)) as raised,
                    ):
                        backend.update_activity(
                            activity.id,
                            status=invalid_status,
                            summary='must not persist',
                            details={'step': 2},
                        )
                    self.assertIn('invalid activity status', str(raised.exception))
                    current = next(item for item in backend.activities() if item.id == activity.id)
                    outcomes.append(
                        (
                            invalid_status,
                            current.status,
                            current.revision,
                            current.summary,
                            current.details,
                        )
                    )

        self.assertEqual(outcomes[:2], outcomes[2:])

    def test_memory_and_django_rebuild_exact_multi_turn_parity(self) -> None:
        """Both backends apply the canonical rebuild filter and provider wire format."""
        session = make_test_session('rebuild-backend-parity')
        backends = [
            DjangoSessionBackend(session),
            MemorySessionBackend(load_example('clock-assistant'), user_id=session.agent.user_id),
        ]
        expected = [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'first'},
            {
                'role': 'assistant',
                'content': 'checking',
                'tool_calls': [
                    {
                        'id': 'call-1',
                        'type': 'function',
                        'function': {
                            'name': 'clock.now',
                            'arguments': {'zone': 'UTC', 'nested': {'value': 1}},
                        },
                    }
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call-1',
                'content': '{"time": "midnight", "zone": "UTC"}',
            },
            {'role': 'user', 'content': 'second'},
            {'role': 'assistant', 'content': 'done'},
        ]

        for backend in backends:
            with self.subTest(backend=type(backend).__name__):
                backend.create_activity(
                    kind='input',
                    status='succeeded',
                    name='input',
                    summary='',
                    details={'content': 'first'},
                )
                backend.create_activity(
                    kind='output',
                    status='succeeded',
                    name='output',
                    summary='',
                    details={'content': 'checking'},
                )
                backend.create_activity(
                    kind='tool',
                    status='succeeded',
                    name='irrelevant-stored-name',
                    summary='',
                    details={
                        'call_id': 'call-1',
                        'instance_id': 'clock',
                        'function': 'now',
                        'arguments': {'zone': 'UTC', 'nested': {'value': 1}},
                        'result': {'zone': 'UTC', 'time': 'midnight'},
                    },
                )
                backend.create_activity(
                    kind='tool',
                    status=AgentSessionActivityStatus.RUNNING,
                    name='clock__pending',
                    summary='',
                    details={
                        'call_id': 'nonterminal',
                        'instance_id': 'clock',
                        'function': 'pending',
                        'arguments': {},
                        'result': 'hidden',
                    },
                )
                backend.create_activity(
                    kind='tool',
                    status='succeeded',
                    name='clock__malformed',
                    summary='',
                    details={'call_id': 'malformed', 'arguments': [], 'result': 'hidden'},
                )
                backend.create_activity(
                    kind='input',
                    status='failed',
                    name='input',
                    summary='',
                    details={'content': 'hidden'},
                )
                backend.create_activity(
                    kind='input',
                    status='succeeded',
                    name='input',
                    summary='',
                    details={'content': 'second'},
                )
                backend.create_activity(
                    kind='output',
                    status='succeeded',
                    name='output',
                    summary='',
                    details={'content': 'done'},
                )

                self.assertEqual(backend.rebuild_messages(system_prompt='system'), expected)

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_service_parity_without_duplicate_publish(self, mock_publish: Any) -> None:
        """Django create/update publish exactly once per persisted revision."""
        session = make_test_session('django-recorder')
        backend = DjangoSessionBackend(session)
        recorder = BackendActivityRecorder(backend)

        parent = recorder.start(kind='tool', name='clock__now', summary='Calling')
        with recorder.push_parent(parent.id):
            note = recorder.status_note(name='checkpoint', summary='Ready')
        completed = recorder.complete(parent.id, summary='Done', details={'result': 'ok'})

        activities = backend.activities()
        self.assertEqual([activity.seq for activity in activities], [1, 2])
        self.assertEqual(next(activity for activity in activities if activity.id == note.id).parent_id, parent.id)
        self.assertEqual(completed.revision, 2)
        self.assertEqual(completed.status, 'succeeded')
        self.assertEqual(mock_publish.call_count, 3)

    def test_backend_refuses_activity_from_another_session(self) -> None:
        """A backend cannot complete an activity owned by another session."""
        first = make_test_session('django-scope-first')
        second = make_test_session('django-scope-second')
        first_recorder = BackendActivityRecorder(DjangoSessionBackend(first))
        second_backend = DjangoSessionBackend(second)
        foreign = second_backend.create_activity(
            kind='tool',
            status='running',
            name='foreign',
            summary='Foreign',
            details={},
        )

        with self.assertRaisesRegex(
            ValueError,
            'activity does not belong to backend session',
        ):
            first_recorder.complete(foreign.id, summary='Not allowed')

        unchanged = second_backend.activities()[0]
        self.assertEqual(unchanged.status, 'running')
        self.assertEqual(unchanged.revision, 1)

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_outer_transaction_freezes_details_and_publish_payload(self, mock_publish: Any) -> None:
        """Scheduled publication and persisted JSON detach from caller/model mutation."""
        session = make_test_session('django-frozen-payload')
        details = {'request': {'query': 'original'}}

        with transaction.atomic():
            row = session_commands.create_activity(
                session,
                kind='span',
                status='running',
                name='frozen',
                summary='Frozen',
                details=details,
            )
            details['request']['query'] = 'caller changed'
            row.details['request']['query'] = 'model changed'
            mock_publish.assert_not_called()

        persisted = AgentSessionActivity.objects.get(pk=row.id)
        self.assertEqual(persisted.details, {'request': {'query': 'original'}})
        self.assertEqual(
            mock_publish.call_args.args[1]['details'],
            {'request': {'query': 'original'}},
        )

    def test_memory_and_django_returns_detach_nested_details(self) -> None:
        """Both backends return details detached from persisted backend state."""
        session = make_test_session('django-memory-copy-parity')
        backends = [
            DjangoSessionBackend(session),
            MemorySessionBackend(load_example('clock-assistant'), user_id=1),
        ]

        for backend in backends:
            with self.subTest(backend=type(backend).__name__):
                details = {'request': {'query': 'original'}}
                returned = backend.create_activity(
                    kind='span',
                    status='running',
                    name='detached',
                    summary='Detached',
                    details=details,
                )
                details['request']['query'] = 'caller changed'
                self.assertEqual(
                    returned.details,
                    {'request': {'query': 'original'}},
                )
                returned.details['request']['query'] = 'return changed'
                self.assertEqual(
                    backend.activities()[0].details,
                    {'request': {'query': 'original'}},
                )

    @patch('apps.runner.dispatch.maybe_dispatch_session')
    def test_link_subagent_uses_atomic_django_command(self, mock_dispatch: Any) -> None:
        """Django recording creates and dispatches one linked child reference."""
        session = make_test_session('django-link')
        recorder = BackendActivityRecorder(DjangoSessionBackend(session))
        assert session.agent_id is not None

        reference = recorder.link_subagent(
            agent_id=session.agent_id,
            name='child',
            summary='Delegate',
        )

        activity = AgentSessionActivity.objects.get(pk=reference.id)
        child_session = activity.child_session
        self.assertIsNotNone(child_session)
        assert child_session is not None
        self.assertEqual(activity.session_id, session.id)
        self.assertEqual(child_session.parent_session_id, session.id)
        mock_dispatch.assert_called_once_with(activity.child_session_id)
