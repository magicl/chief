# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.sessions.events import append_event
from apps.sessions.models import AgentSessionEventKind
from apps.sessions.rebuild import rebuild_messages
from apps.sessions.tests.base import make_test_session
from libs.agent_spec import load_example

from olib.py.django.test.cases import OTransactionTestCase


class TestRebuildMessages(OTransactionTestCase):
    def test_full_conversation_with_tool_pair(self) -> None:
        session = make_test_session()
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'What time is it?'})
        append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'Let me check.'})
        append_event(
            session,
            AgentSessionEventKind.TOOL_CALL,
            {'call_id': 'c1', 'tool': 'clock', 'function': 'now', 'arguments': {}},
        )
        append_event(
            session, AgentSessionEventKind.TOOL_RESULT, {'call_id': 'c1', 'content': '2026-01-01T00:00:00+00:00'}
        )
        append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'It is midnight UTC.'})

        messages = rebuild_messages(session, system_prompt=load_example('clock-assistant').system_prompt)
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1], {'role': 'user', 'content': 'What time is it?'})
        self.assertEqual(messages[2]['role'], 'assistant')
        self.assertEqual(messages[2]['content'], 'Let me check.')
        self.assertIn('tool_calls', messages[2])
        self.assertEqual(messages[3]['role'], 'tool')
        self.assertEqual(messages[3]['tool_call_id'], 'c1')
        self.assertEqual(messages[4], {'role': 'assistant', 'content': 'It is midnight UTC.'})

    def test_failure_and_restart_omitted(self) -> None:
        session = make_test_session('other-agent')
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'go'})
        append_event(session, AgentSessionEventKind.FAILURE, {'message': 'boom'})
        append_event(session, AgentSessionEventKind.RESTART, {})
        append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'ok'})

        messages = rebuild_messages(session, system_prompt='sys')
        roles = [m['role'] for m in messages]
        self.assertEqual(roles, ['system', 'user', 'assistant'])
