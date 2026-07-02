# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any
from unittest.mock import patch

from libs.providers.base import StreamResult
from apps.runner.tasks import run_session
from apps.sessions.events import append_event, events_for
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from apps.sessions.tests.base import make_test_session

from olib.py.django.test.cases import OTransactionTestCase


class TestRunSessionResumability(OTransactionTestCase):
    @patch('apps.runner.backends.django.publish_session_event')
    @patch('apps.runner.backends.django.mailbox_drain', return_value=[])
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.runner.tasks.release_lock')
    @patch('apps.runner.tasks.try_acquire_lock', return_value=True)
    @patch('apps.runner.loop.make_provider')
    def test_resume_emits_restart_and_reuses_events(
        self,
        mock_provider_cls: Any,
        _lock: Any,
        _unlock: Any,
        _mailbox: Any,
        _publish: Any,
    ) -> None:
        session = make_test_session('resume-agent')
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'hello'})
        append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'prior turn'})
        session.status = AgentSessionStatus.WAITING
        session.save(update_fields=['status'])

        result = StreamResult(content='continued', latency_ms=1)
        mock_provider_cls.return_value.collect.return_value = result

        run_session.run(str(session.id))

        session.refresh_from_db()
        kinds = [e.kind for e in events_for(session)]
        self.assertIn(AgentSessionEventKind.RESTART, kinds)
        self.assertEqual(kinds.count(AgentSessionEventKind.OUTPUT), 2)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)

    @patch('apps.runner.backends.django.publish_session_event')
    @patch('apps.runner.backends.django.mailbox_drain', return_value=[])
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.runner.tasks.release_lock')
    @patch('apps.runner.tasks.try_acquire_lock', return_value=True)
    @patch('apps.runner.loop.make_provider')
    def test_first_run_without_input_waits_for_chat(
        self,
        mock_provider_cls: Any,
        _lock: Any,
        _unlock: Any,
        _mailbox: Any,
        _publish: Any,
    ) -> None:
        session = make_test_session('fresh-agent')
        run_session.run(str(session.id))

        session.refresh_from_db()
        kinds = [e.kind for e in events_for(session)]
        self.assertNotIn(AgentSessionEventKind.RESTART, kinds)
        self.assertNotIn(AgentSessionEventKind.OUTPUT, kinds)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)
        mock_provider_cls.assert_not_called()
