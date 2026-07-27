# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Linked child-session creation and reconciliation tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from apps.agents.models import Agent, AgentStatus, TriggerStatus
from apps.agents.services.config_commands import create_from_example
from apps.runner.activity_recorder import BackendActivityRecorder
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.backends.memory import MemorySessionBackend
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
    TriggerType,
)
from apps.sessions.services import commands as session_commands
from apps.sessions.services import queries as session_queries
from apps.sessions.tests.base import make_test_session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from libs.agent_spec import load_example

from olib.py.django.test.cases import OTransactionTestCase


class TestLinkedChildSession(OTransactionTestCase):
    """Exercise the atomic command and backend recorder boundary."""

    def _child_agent(self, parent: AgentSession, suffix: str = 'child') -> Agent:
        """Create another configured agent owned by the parent's user."""
        return create_from_example(
            parent.agent.user,
            'clock-assistant',
            identifier=f'{parent.agent.identifier}-{suffix}',
        )

    def _span(self, parent: AgentSession) -> AgentSessionActivity:
        """Create a parent scope for a linked child reference."""
        return session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='delegate',
            summary='Starting child',
            details={},
        )

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_start_linked_child_session_is_atomic_and_nested(self, mock_publish: Any) -> None:
        """One command creates direct ancestry and one nested parent reference."""
        parent = make_test_session('sub-parent')
        span = self._span(parent)
        child_agent = self._child_agent(parent)
        mock_publish.reset_mock()

        child = session_commands.start_linked_child_session(
            parent_session=parent,
            parent_activity_id=span.id,
            agent=child_agent,
            name='research',
            summary='Research delegated work',
            details={'topic': 'safe'},
            dispatch=False,
        )

        reference = AgentSessionActivity.objects.get(child_session=child)
        self.assertEqual(child.parent_session_id, parent.id)
        self.assertEqual(child.agent_id, child_agent.id)
        self.assertEqual(child.agent_config_id, child_agent.current_config_id)
        self.assertEqual(child.trigger_type, TriggerType.TOOL_CALL)
        self.assertEqual(reference.session_id, parent.id)
        self.assertEqual(reference.parent_id, span.id)
        self.assertEqual(reference.status, AgentSessionActivityStatus.PENDING)
        self.assertEqual(reference.revision, 1)
        self.assertEqual(reference.details, {'topic': 'safe', 'child_status': AgentSessionStatus.QUEUED})
        mock_publish.assert_called_once()
        payload = mock_publish.call_args.args[1]
        self.assertEqual(payload['revision'], 1)
        self.assertEqual(payload['child_session_id'], str(child.id))

    def test_root_stays_unlinked_and_queries_expose_owned_tree(self) -> None:
        """Root, child-list, and nearest-first breadcrumb queries preserve ownership."""
        root = make_test_session('sub-query-root')
        child_agent = self._child_agent(root)
        child = session_commands.start_linked_child_session(
            parent_session=root,
            agent=child_agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        grandchild = session_commands.start_linked_child_session(
            parent_session=child,
            agent=child_agent,
            name='grandchild',
            summary='Grandchild',
            dispatch=False,
        )

        self.assertIsNone(root.parent_session_id)
        self.assertEqual(
            session_queries.child_sessions_for(root, user_id=root.agent.user_id),
            [child],
        )
        self.assertEqual(
            session_queries.parent_session_breadcrumb(
                grandchild,
                user_id=root.agent.user_id,
            ),
            [child, root],
        )
        self.assertEqual(session_queries.child_sessions_for(root, user_id=-1), [])
        self.assertEqual(session_queries.parent_session_breadcrumb(grandchild, user_id=-1), [])

    def test_child_session_can_have_only_one_parent_reference(self) -> None:
        """The one-to-one link prevents a child from appearing in two activities."""
        parent = make_test_session('sub-unique')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )

        with self.assertRaises(IntegrityError):
            session_commands.create_activity(
                parent,
                kind=AgentSessionActivityKind.SUBAGENT,
                status=AgentSessionActivityStatus.RUNNING,
                name='duplicate',
                summary='Duplicate',
                details={},
                child_session_id=child.id,
            )

    def test_rejects_cross_user_agent(self) -> None:
        """A parent cannot delegate to an agent owned by another user."""
        parent = make_test_session('sub-owner-parent')
        other = make_test_session('sub-owner-other')

        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=other.agent,
                name='foreign',
                summary='Foreign',
                dispatch=False,
            )

        self.assertFalse(parent.child_sessions.exists())
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    def test_corrupt_ancestor_cycle_rejects_before_writes_or_dispatch(self) -> None:
        """The locked cycle walker catches a database-corrupted ancestor chain."""
        parent = make_test_session('sub-cycle-parent')
        ancestor = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        # Bypass model immutability deliberately: parent -> ancestor -> parent.
        AgentSession.objects.filter(pk=parent.id).update(parent_session_id=ancestor.id)
        parent.refresh_from_db()
        session_count = AgentSession.objects.count()

        with patch('apps.runner.dispatch.maybe_dispatch_session') as dispatch:
            with self.assertRaises(ValidationError) as raised:
                session_commands.start_linked_child_session(
                    parent_session=parent,
                    agent=parent.agent,
                    name='cycle-child',
                    summary='Cycle child',
                    dispatch_callback=dispatch,
                )
            dispatch.assert_not_called()

        self.assertEqual(
            raised.exception.message_dict,
            {'parent_session': ['session ancestry cannot contain a cycle']},
        )
        self.assertEqual(AgentSession.objects.count(), session_count)
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    def test_rejects_missing_and_cross_session_parent_activity(self) -> None:
        """A child reference may nest only under an existing parent-session activity."""
        parent = make_test_session('sub-parent-scope')
        other = make_test_session('sub-other-scope')
        foreign_span = self._span(other)

        for parent_activity_id in (uuid4(), foreign_span.id):
            with self.subTest(parent_activity_id=parent_activity_id):
                with self.assertRaises(ValidationError):
                    session_commands.start_linked_child_session(
                        parent_session=parent,
                        parent_activity_id=parent_activity_id,
                        agent=parent.agent,
                        name='invalid-scope',
                        summary='Invalid scope',
                        dispatch=False,
                    )

        self.assertFalse(parent.child_sessions.exists())
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    def test_rejects_invalid_trigger_agent_config_and_status(self) -> None:
        """Optional trigger metadata must be active and pinned to the selected child."""
        parent = make_test_session('sub-trigger-parent')
        child_agent = self._child_agent(parent)
        other_agent = self._child_agent(parent, 'other')
        foreign_trigger = other_agent.triggers.get(name='manual')

        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=child_agent,
                trigger=foreign_trigger,
                name='child',
                summary='Child',
                dispatch=False,
            )

        trigger = child_agent.triggers.get(name='manual')
        trigger.status = TriggerStatus.DISABLED
        trigger.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=child_agent,
                trigger=trigger,
                name='child',
                summary='Child',
                dispatch=False,
            )

        self.assertFalse(parent.child_sessions.exists())

    def test_missing_config_and_disabled_agent_roll_back_before_linkage(self) -> None:
        """Startup validation failures leave neither a child nor a reference."""
        parent = make_test_session('sub-startup-parent')
        child_agent = self._child_agent(parent)
        child_agent.current_config = None
        child_agent.save(update_fields=['current_config'])

        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=child_agent,
                name='child',
                summary='Child',
                dispatch=False,
            )

        child_agent.refresh_from_db()
        child_agent.status = AgentStatus.DISABLED
        child_agent.current_config = child_agent.configs.first()
        child_agent.save(update_fields=['status', 'current_config'])
        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=child_agent,
                name='child',
                summary='Child',
                dispatch=False,
            )

        self.assertFalse(parent.child_sessions.exists())
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    @patch('apps.runner.dispatch.maybe_dispatch_session')
    def test_dispatch_runs_exactly_once_after_commit(self, mock_dispatch: Any) -> None:
        """A committed linked start freezes its identity and dispatches once."""
        parent = make_test_session('sub-dispatch')

        with transaction.atomic():
            child = session_commands.start_linked_child_session(
                parent_session=parent,
                agent=parent.agent,
                name='child',
                summary='Child',
                dispatch_callback=mock_dispatch,
            )
            child_id = child.id
            child.id = uuid4()
            mock_dispatch.assert_not_called()

        mock_dispatch.assert_called_once_with(child_id)

    @patch('apps.runner.dispatch.maybe_dispatch_session')
    def test_dispatch_is_discarded_on_outer_rollback(self, mock_dispatch: Any) -> None:
        """An outer rollback discards both linkage rows and its on-commit dispatch."""
        parent = make_test_session('sub-dispatch-rollback')

        with self.assertRaises(IntegrityError), transaction.atomic():
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=parent.agent,
                name='child',
                summary='Child',
                dispatch_callback=mock_dispatch,
            )
            raise IntegrityError('force rollback')

        mock_dispatch.assert_not_called()
        self.assertFalse(AgentSession.objects.filter(parent_session=parent).exists())
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    @patch('apps.runner.dispatch.maybe_dispatch_session', return_value=False)
    def test_dispatch_false_marks_startup_failure(self, mock_dispatch: Any) -> None:
        """A refused post-commit dispatch becomes one durable child failure."""
        parent = make_test_session('sub-dispatch-false')

        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch_callback=mock_dispatch,
        )

        child.refresh_from_db()
        reference = AgentSessionActivity.objects.get(child_session=child)
        self.assertEqual(child.status, AgentSessionStatus.WAITING)
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(reference.summary, 'Child startup failed')
        self.assertEqual(reference.details['failure_code'], 'child_dispatch_failed')

    @patch('apps.runner.dispatch.maybe_dispatch_session', side_effect=RuntimeError('unsafe detail'))
    def test_dispatch_raise_marks_startup_failure_without_propagating(self, mock_dispatch: Any) -> None:
        """A raised post-commit dispatch records a stable failure without escaping."""
        parent = make_test_session('sub-dispatch-raise')

        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch_callback=mock_dispatch,
        )

        child.refresh_from_db()
        reference = AgentSessionActivity.objects.get(child_session=child)
        self.assertEqual(child.status, AgentSessionStatus.WAITING)
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertNotIn('unsafe detail', str(reference.details))

    def test_missing_dispatch_setup_rejects_before_writes(self) -> None:
        """A requested start without a runner callback cannot leave a half-link."""
        parent = make_test_session('sub-dispatch-setup')

        with self.assertRaises(ValidationError):
            session_commands.start_linked_child_session(
                parent_session=parent,
                agent=parent.agent,
                name='child',
                summary='Child',
            )

        self.assertFalse(AgentSession.objects.filter(parent_session=parent).exists())
        self.assertFalse(parent.activities.filter(kind=AgentSessionActivityKind.SUBAGENT).exists())

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_backend_status_reconciles_revision_and_parent_publication(self, mock_publish: Any) -> None:
        """Child backend status changes revise and publish only the parent reference."""
        parent = make_test_session('sub-reconcile')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        reference = AgentSessionActivity.objects.get(child_session=child)
        mock_publish.reset_mock()

        DjangoSessionBackend(child).set_status(AgentSessionStatus.RUNNING)
        reference.refresh_from_db()

        self.assertEqual(reference.status, AgentSessionActivityStatus.RUNNING)
        self.assertEqual(reference.summary, f'{child.agent.name}: running')
        self.assertEqual(reference.details['child_status'], AgentSessionStatus.RUNNING)
        self.assertEqual(reference.revision, 2)
        mock_publish.assert_called_once()
        self.assertEqual(mock_publish.call_args.args[0], parent.id)
        self.assertEqual(mock_publish.call_args.args[1]['id'], str(reference.id))

        mock_publish.reset_mock()
        DjangoSessionBackend(child).set_status(AgentSessionStatus.DONE)
        reference.refresh_from_db()
        self.assertEqual(reference.status, AgentSessionActivityStatus.SUCCEEDED)
        self.assertEqual(reference.revision, 3)
        mock_publish.assert_called_once()

    def test_normal_waiting_remains_active_but_failure_waiting_is_terminal(self) -> None:
        """Actual waiting semantics distinguish user wait from runtime failure."""
        normal_parent = make_test_session('sub-normal-wait-parent')
        normal_child = session_commands.start_linked_child_session(
            parent_session=normal_parent,
            agent=normal_parent.agent,
            name='normal-child',
            summary='Normal child',
            dispatch=False,
        )
        DjangoSessionBackend(normal_child).set_status(AgentSessionStatus.WAITING)
        normal_reference = AgentSessionActivity.objects.get(child_session=normal_child)
        self.assertEqual(normal_reference.status, AgentSessionActivityStatus.RUNNING)

        failed_parent = make_test_session('sub-failure-wait-parent')
        failed_child = session_commands.start_linked_child_session(
            parent_session=failed_parent,
            agent=failed_parent.agent,
            name='failed-child',
            summary='Failed child',
            dispatch=False,
        )
        session_commands.create_activity(
            failed_child,
            kind=AgentSessionActivityKind.FAILURE,
            status=AgentSessionActivityStatus.FAILED,
            name='failure',
            summary='Child runtime failed',
            details={'code': 'runtime_failure'},
        )
        DjangoSessionBackend(failed_child).set_status(AgentSessionStatus.WAITING)
        failed_reference = AgentSessionActivity.objects.get(child_session=failed_child)
        self.assertEqual(failed_reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(failed_reference.details['failure_code'], 'runtime_failure')

        DjangoSessionBackend(failed_child).set_status(AgentSessionStatus.DONE)
        failed_reference.refresh_from_db()
        self.assertEqual(failed_reference.status, AgentSessionActivityStatus.FAILED)

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_terminal_reference_does_not_regress_to_running(self, mock_publish: Any) -> None:
        """A stale active transition cannot reopen a failed parent reference."""
        parent = make_test_session('sub-stale-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        session_commands.create_activity(
            child,
            kind=AgentSessionActivityKind.FAILURE,
            status=AgentSessionActivityStatus.FAILED,
            name='failure',
            summary='Child runtime failed',
            details={'code': 'runtime_failure'},
        )
        DjangoSessionBackend(child).set_status(AgentSessionStatus.WAITING)
        reference = AgentSessionActivity.objects.get(child_session=child)
        failed_revision = reference.revision
        mock_publish.reset_mock()

        DjangoSessionBackend(child).set_status(AgentSessionStatus.RUNNING)

        reference.refresh_from_db()
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(reference.revision, failed_revision)
        mock_publish.assert_not_called()

    def test_unlinked_subagent_row_cannot_reconcile_a_child(self) -> None:
        """Reconciliation requires the exact child-linked reference row."""
        parent = make_test_session('sub-unlinked-parent')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            parent_session=parent,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TOOL_CALL,
        )
        unlinked = session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.RUNNING,
            name='unlinked',
            summary='Unlinked',
            details={},
        )

        self.assertIsNone(session_commands.reconcile_subagent_activity(child))
        unlinked.refresh_from_db()
        self.assertEqual(unlinked.revision, 1)

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_direct_child_delete_publishes_unavailable_reference(self, mock_publish: Any) -> None:
        """Direct deletion reconciles only after the collector transaction commits."""
        parent = make_test_session('sub-delete-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        child_id = child.id
        reference = AgentSessionActivity.objects.get(child_session=child)
        mock_publish.reset_mock()

        with transaction.atomic():
            child.delete()
            reference.refresh_from_db()
            self.assertIsNone(reference.child_session_id)
            self.assertEqual(reference.status, AgentSessionActivityStatus.PENDING)
            mock_publish.assert_not_called()

        reference.refresh_from_db()
        self.assertIsNone(reference.child_session_id)
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(reference.details['prior_child_session_id'], str(child_id))
        self.assertEqual(reference.revision, 2)
        mock_publish.assert_called_once()
        payload = mock_publish.call_args.args[1]
        self.assertIsNone(payload['child_session_id'])
        self.assertEqual(payload['revision'], 2)

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_child_agent_delete_reconciles_surviving_parent(self, mock_publish: Any) -> None:
        """An Agent cascade still finalizes a reference owned by another agent."""
        parent = make_test_session('sub-agent-delete-parent')
        child_agent = self._child_agent(parent, 'deleted-agent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=child_agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        child_id = child.id
        reference = AgentSessionActivity.objects.get(child_session=child)
        mock_publish.reset_mock()

        child_agent.delete()

        self.assertTrue(AgentSession.objects.filter(pk=parent.id).exists())
        reference.refresh_from_db()
        self.assertIsNone(reference.child_session_id)
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(reference.details['prior_child_session_id'], str(child_id))
        mock_publish.assert_called_once()

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_queryset_child_delete_reconciles_surviving_parent(self, mock_publish: Any) -> None:
        """QuerySet deletion receives the same post-collector reconciliation."""
        parent = make_test_session('sub-queryset-delete-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        child_id = child.id
        reference = AgentSessionActivity.objects.get(child_session=child)
        mock_publish.reset_mock()

        AgentSession.objects.filter(pk=child.id).delete()

        reference.refresh_from_db()
        self.assertIsNone(reference.child_session_id)
        self.assertEqual(reference.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(reference.details['prior_child_session_id'], str(child_id))
        mock_publish.assert_called_once()

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_parent_tree_delete_does_not_publish_child_unavailability(self, mock_publish: Any) -> None:
        """Cascading a whole tree emits no reference update for deleted parents."""
        parent = make_test_session('sub-cascade-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        mock_publish.reset_mock()

        parent.delete()

        self.assertFalse(AgentSession.objects.filter(pk=child.id).exists())
        mock_publish.assert_not_called()

    def test_child_status_and_reconcile_lock_activity_before_session(self) -> None:
        """Both mutation paths acquire the parent reference before the child row."""
        parent = make_test_session('sub-lock-order-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        original_activity_lock = AgentSessionActivity.objects.select_for_update
        original_session_lock = AgentSession.objects.select_for_update

        def lock_recorder(label: str, target: Any, order: list[str]) -> Any:
            """Build one lock wrapper that records before delegating."""

            def record_lock(*args: Any, **kwargs: Any) -> Any:
                """Record and perform one select-for-update call."""
                order.append(label)
                return target(*args, **kwargs)

            return record_lock

        for operation in (
            lambda: session_commands.set_session_status(child, AgentSessionStatus.RUNNING),
            lambda: session_commands.reconcile_subagent_activity(child),
        ):
            lock_order: list[str] = []

            with (
                patch.object(
                    AgentSessionActivity.objects,
                    'select_for_update',
                    side_effect=lock_recorder('activity', original_activity_lock, lock_order),
                ),
                patch.object(
                    AgentSession.objects,
                    'select_for_update',
                    side_effect=lock_recorder('session', original_session_lock, lock_order),
                ),
            ):
                operation()

            self.assertGreaterEqual(len(lock_order), 2)
            self.assertEqual(lock_order[:2], ['activity', 'session'])

        with patch.object(
            AgentSessionActivity.objects,
            'select_for_update',
            wraps=original_activity_lock,
        ) as root_activity_lock:
            session_commands.set_session_status(parent, AgentSessionStatus.RUNNING)

        root_activity_lock.assert_not_called()

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_parent_stream_receives_no_child_activity_rows(self, mock_publish: Any) -> None:
        """Child work publishes on the child stream; the parent contains only its reference."""
        parent = make_test_session('sub-stream-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            name='child',
            summary='Child',
            dispatch=False,
        )
        mock_publish.reset_mock()

        DjangoSessionBackend(child).create_activity(
            kind=AgentSessionActivityKind.STATUS,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='child-note',
            summary='Child note',
            details={},
        )

        mock_publish.assert_called_once()
        self.assertEqual(mock_publish.call_args.args[0], child.id)
        self.assertEqual(
            [activity.kind for activity in session_queries.activities_for(parent)],
            [AgentSessionActivityKind.SUBAGENT],
        )

    @patch('apps.runner.dispatch.maybe_dispatch_session')
    def test_recorder_uses_current_scope_for_django_link(self, mock_dispatch: Any) -> None:
        """Django recorder links the child reference beneath its current parent."""
        parent = make_test_session('sub-recorder-django')
        child_agent = self._child_agent(parent)
        recorder = BackendActivityRecorder(DjangoSessionBackend(parent))
        span = recorder.start(kind='span', name='delegate', summary='Delegate')

        with recorder.push_parent(span.id):
            reference = recorder.link_subagent(
                agent_id=child_agent.id,
                name='child',
                summary='Child',
                details={'safe': True},
            )

        row = AgentSessionActivity.objects.get(pk=reference.id)
        self.assertEqual(row.parent_id, span.id)
        child_session = row.child_session
        self.assertIsNotNone(child_session)
        assert child_session is not None
        self.assertEqual(child_session.parent_session_id, parent.id)
        mock_dispatch.assert_called_once_with(row.child_session_id)

    def test_memory_recorder_simulates_link_reference(self) -> None:
        """Memory linking creates a unique child reference without a Django child run."""
        backend = MemorySessionBackend(load_example('clock-assistant'), user_id=1)
        recorder = BackendActivityRecorder(backend)
        span = recorder.start(kind='span', name='delegate', summary='Delegate')

        with recorder.push_parent(span.id):
            reference = recorder.link_subagent(
                agent_id=uuid4(),
                name='child',
                summary='Child',
                details={'safe': True},
            )

        row = next(activity for activity in backend.activities() if activity.id == reference.id)
        self.assertEqual(row.kind, 'subagent')
        self.assertEqual(row.status, 'pending')
        self.assertEqual(row.parent_id, span.id)
        self.assertIsNotNone(row.child_session_id)
        self.assertEqual(row.details['memory_link_only'], True)
