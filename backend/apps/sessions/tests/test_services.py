# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any
from unittest.mock import patch

from apps.sessions.events import append_event
from apps.sessions.models import AgentSessionEventKind
from apps.sessions.services.commands import record_input, update_session_name
from apps.sessions.services.queries import get_first_input_text, get_session_name
from apps.sessions.tests.base import make_test_session

from olib.py.django.test.cases import OTransactionTestCase


class TestSessionServiceCommands(OTransactionTestCase):
    @patch('apps.sessions.services.commands.publish_session_event')
    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_record_input_schedules_name_on_first_message(self, mock_schedule: Any, _publish: Any) -> None:
        session = make_test_session('name-cmd-agent')
        record_input(session, 'hello world')
        mock_schedule.assert_called_once_with(session.id)

    @patch('apps.sessions.services.commands.publish_session_event')
    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_record_input_skips_schedule_on_second_message(self, mock_schedule: Any, _publish: Any) -> None:
        session = make_test_session('name-cmd-agent-2')
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
    def test_get_first_input_text(self) -> None:
        session = make_test_session('name-query-agent')
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'What is the weather?'})
        self.assertEqual(get_first_input_text(session.id), 'What is the weather?')
        self.assertIsNone(get_session_name(session.id))
