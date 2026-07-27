# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import inspect
from typing import Any
from unittest.mock import patch

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.notify import publish_session_activity
from apps.sessions.services.commands import (
    create_activity,
    record_input,
    update_session_name,
)
from apps.sessions.services.queries import (
    child_sessions_for,
    get_first_input_text,
    get_session_name,
    input_activity_count,
    parent_session_breadcrumb,
)
from apps.sessions.tests.base import make_test_session
from django.db import transaction

from olib.py.django.test.cases import OTransactionTestCase


class TestSessionServiceCommands(OTransactionTestCase):
    @patch('apps.sessions.notify.publish_session_message')
    def test_activity_publisher_wraps_upsert_operation(self, mock_publish: Any) -> None:
        """The activity publisher uses the typed full-state upsert envelope."""
        session = make_test_session('activity-envelope')
        activity = {'id': 'activity-1', 'revision': 2}

        publish_session_activity(session.id, activity)

        mock_publish.assert_called_once_with(
            session.id,
            {
                'channel': 'session_activity',
                'payload': {'operation': 'upsert', 'activity': activity},
            },
        )

    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_record_input_schedules_name_on_first_message(self, mock_schedule: Any) -> None:
        session = make_test_session('name-cmd-agent')
        record_input(session, 'hello world')
        mock_schedule.assert_called_once_with(session.id)

    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_record_input_skips_schedule_on_second_message(self, mock_schedule: Any) -> None:
        session = make_test_session('name-cmd-agent-2')
        record_input(session, 'first')
        record_input(session, 'second')
        mock_schedule.assert_called_once_with(session.id)

    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_record_input_creates_terminal_activity(self, _schedule: Any) -> None:
        """Input persistence uses the canonical lowercase activity envelope."""
        session = make_test_session('input-activity')
        content = 'x' * 121

        row = record_input(session, content)

        self.assertEqual(row.kind, AgentSessionActivityKind.INPUT)
        self.assertEqual(row.status, AgentSessionActivityStatus.SUCCEEDED)
        self.assertEqual(row.name, 'input')
        self.assertEqual(row.summary, ('x' * 120) + '…')
        self.assertEqual(row.details, {'content': content})
        self.assertEqual(row.revision, 1)

    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_first_input_decision_holds_transaction_after_prior_activity(self, mock_schedule: Any) -> None:
        """First-input scheduling stays atomic even when another activity has seq one."""
        session = make_test_session('input-after-status')
        create_activity(
            session,
            kind=AgentSessionActivityKind.STATUS,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='ready',
            summary='',
            details={},
        )

        def count_inside_transaction(session_id: Any) -> int:
            """Verify the count runs before the activity transaction releases its lock."""
            self.assertTrue(transaction.get_connection().in_atomic_block)
            return input_activity_count(session_id)

        with patch(
            'apps.sessions.services.commands.input_activity_count',
            side_effect=count_inside_transaction,
        ):
            record_input(session, 'first')
            record_input(session, 'second')

        mock_schedule.assert_called_once_with(session.id)

    @patch('apps.sessions.services.commands.publish_session_update')
    def test_update_session_name_publishes_patch(self, mock_publish: Any) -> None:
        session = make_test_session('name-update-agent')
        updated = update_session_name(session.id, 'Budget planning')
        self.assertTrue(updated)
        session.refresh_from_db()
        self.assertEqual(session.name, 'Budget planning')
        mock_publish.assert_called_once_with(session.id, {'name': 'Budget planning'})

    @patch('apps.sessions.services.commands.publish_session_update')
    def test_update_session_name_is_idempotent(self, mock_publish: Any) -> None:
        session = make_test_session('name-idempotent-agent')
        update_session_name(session.id, 'First title')
        updated = update_session_name(session.id, 'Second title')
        self.assertFalse(updated)
        session.refresh_from_db()
        self.assertEqual(session.name, 'First title')
        mock_publish.assert_called_once()


class TestSessionServiceQueries(OTransactionTestCase):
    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_get_first_input_text(self, _schedule: Any) -> None:
        """Input helpers read trimmed content from activity details."""
        session = make_test_session('name-query-agent')
        record_input(session, '  What is the weather?  ')
        self.assertEqual(get_first_input_text(session.id), 'What is the weather?')
        self.assertEqual(input_activity_count(session.id), 1)
        self.assertIsNone(get_session_name(session.id))

    def test_parent_session_breadcrumb_is_nearest_first(self) -> None:
        """Breadcrumb traversal returns direct parent before older ancestors."""
        root = make_test_session('breadcrumb')
        child = AgentSession.objects.create(
            agent=root.agent,
            agent_config=root.agent_config,
            status=root.status,
            trigger_type=root.trigger_type,
            parent_session=root,
        )
        grandchild = AgentSession.objects.create(
            agent=root.agent,
            agent_config=root.agent_config,
            status=root.status,
            trigger_type=root.trigger_type,
            parent_session=child,
        )

        self.assertEqual(
            parent_session_breadcrumb(grandchild, user_id=root.agent.user_id),
            [child, root],
        )

    def test_tree_queries_require_an_explicit_owner(self) -> None:
        """Public tree traversal has no ownership-free call shape."""
        for query in (parent_session_breadcrumb, child_sessions_for):
            with self.subTest(query=query.__name__):
                parameter = inspect.signature(query).parameters['user_id']
                self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_parent_session_breadcrumb_stops_at_cycle(self) -> None:
        """Corrupt ancestry cannot make breadcrumb traversal loop forever."""
        root = make_test_session('breadcrumb-cycle')
        child = AgentSession.objects.create(
            agent=root.agent,
            agent_config=root.agent_config,
            status=root.status,
            trigger_type=root.trigger_type,
            parent_session=root,
        )
        AgentSession.objects.filter(pk=root.id).update(parent_session=child)
        child.refresh_from_db()

        self.assertEqual(
            parent_session_breadcrumb(child, user_id=root.agent.user_id),
            [root],
        )
