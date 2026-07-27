# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any
from unittest.mock import patch

from apps.sessions.models import AgentSessionActivityKind, AgentSessionActivityStatus
from apps.sessions.services.commands import create_activity
from apps.sessions.tasks import generate_session_name
from apps.sessions.tests.base import make_test_session

from olib.py.django.test.cases import OTransactionTestCase


class TestGenerateSessionNameTask(OTransactionTestCase):
    @patch('apps.sessions.services.commands.publish_session_update')
    @patch('apps.sessions.tasks.generate_chat_name', return_value='Password reset help')
    def test_task_sets_session_name(self, _mock_generate: Any, _mock_publish: Any) -> None:
        session = make_test_session('name-task-agent')
        create_activity(
            session,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
            summary='How do I reset my password?',
            details={'content': 'How do I reset my password?'},
        )
        generate_session_name.run(str(session.id))
        session.refresh_from_db()
        self.assertEqual(session.name, 'Password reset help')

    @patch('apps.sessions.tasks.generate_chat_name', return_value='Already named')
    def test_task_skips_when_name_exists(self, mock_generate: Any) -> None:
        session = make_test_session('name-task-skip-agent')
        session.name = 'Existing'
        session.save(update_fields=['name'])
        generate_session_name.run(str(session.id))
        mock_generate.assert_not_called()
