# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any
from uuid import UUID

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.rebuild import (
    rebuild_messages,
    rebuild_messages_from_activities,
)
from apps.sessions.services import commands as session_commands
from apps.sessions.tests.base import make_test_session
from libs.agent_spec import load_example

from olib.py.django.test.cases import OTransactionTestCase


class TestRebuildMessages(OTransactionTestCase):
    def _activity(
        self,
        session: AgentSession,
        *,
        kind: str,
        details: dict[str, Any],
        status: str = AgentSessionActivityStatus.SUCCEEDED,
        parent_id: UUID | None = None,
        name: str | None = None,
    ) -> AgentSessionActivity:
        """Create one activity with concise defaults for rebuild scenarios."""
        return session_commands.create_activity(
            session,
            kind=kind,
            status=status,
            name=name or kind,
            summary='',
            details=details,
            parent_id=parent_id,
        )

    def test_unified_tool_expands_to_assistant_and_tool_messages(self) -> None:
        session = make_test_session()
        self._activity(
            session,
            kind=AgentSessionActivityKind.INPUT,
            details={'content': 'What time is it?'},
        )
        self._activity(
            session,
            kind=AgentSessionActivityKind.OUTPUT,
            details={'content': 'Let me check.'},
        )
        self._activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            details={
                'call_id': 'c1',
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
                'result': {'zone': 'UTC', 'time': '2026-01-01T00:00:00+00:00'},
            },
        )
        self._activity(
            session,
            kind=AgentSessionActivityKind.OUTPUT,
            details={'content': 'It is midnight UTC.'},
        )

        messages = rebuild_messages(session, system_prompt=load_example('clock-assistant').system_prompt)
        self.assertEqual(
            messages,
            [
                {'role': 'system', 'content': load_example('clock-assistant').system_prompt},
                {'role': 'user', 'content': 'What time is it?'},
                {
                    'role': 'assistant',
                    'content': 'Let me check.',
                    'tool_calls': [
                        {
                            'id': 'c1',
                            'type': 'function',
                            'function': {'name': 'clock.now', 'arguments': {}},
                        }
                    ],
                },
                {
                    'role': 'tool',
                    'tool_call_id': 'c1',
                    'content': '{"time": "2026-01-01T00:00:00+00:00", "zone": "UTC"}',
                },
                {'role': 'assistant', 'content': 'It is midnight UTC.'},
            ],
        )

    def test_failed_tool_with_complete_result_is_reconstructed(self) -> None:
        session = make_test_session('failed-tool')
        self._activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.FAILED,
            details={
                'call_id': 'failed-1',
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {'zone': 'UTC'},
                'result': '{"failure":"denied"}',
            },
        )

        messages = rebuild_messages(session, system_prompt='sys')

        self.assertEqual(messages[1]['tool_calls'][0]['id'], 'failed-1')
        self.assertEqual(messages[2]['content'], '{"failure":"denied"}')

    def test_container_kinds_are_ignored_and_nested_output_keeps_seq_order(self) -> None:
        session = make_test_session('rebuild-containers')
        self._activity(
            session,
            kind=AgentSessionActivityKind.INPUT,
            details={'content': 'go'},
        )
        llm = self._activity(
            session,
            kind=AgentSessionActivityKind.LLM,
            details={},
            name='gpt',
        )
        for kind in (
            AgentSessionActivityKind.SPAN,
            AgentSessionActivityKind.STATUS,
            AgentSessionActivityKind.SUBAGENT,
        ):
            self._activity(session, kind=kind, details={}, parent_id=llm.id)
        self._activity(
            session,
            kind=AgentSessionActivityKind.OUTPUT,
            details={'content': 'nested first'},
            parent_id=llm.id,
        )
        self._activity(
            session,
            kind=AgentSessionActivityKind.OUTPUT,
            details={'content': 'top-level second'},
        )

        messages = rebuild_messages(session, system_prompt='sys')

        self.assertEqual(
            messages,
            [
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'go'},
                {'role': 'assistant', 'content': 'nested first'},
                {'role': 'assistant', 'content': 'top-level second'},
            ],
        )

    def test_incomplete_and_non_reconstructable_tools_are_skipped_safely(self) -> None:
        session = make_test_session('rebuild-incomplete')
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                AgentSessionActivityStatus.RUNNING,
                {
                    'call_id': 'running',
                    'instance_id': 'clock',
                    'function': 'now',
                    'arguments': {},
                    'result': 'not final',
                },
            ),
            (
                AgentSessionActivityStatus.PENDING,
                {
                    'call_id': 'pending',
                    'instance_id': 'clock',
                    'function': 'now',
                    'arguments': {},
                    'result': 'not started',
                },
            ),
            (
                AgentSessionActivityStatus.CANCELLED,
                {
                    'call_id': 'cancelled',
                    'instance_id': 'clock',
                    'function': 'now',
                    'arguments': {},
                    'result': 'cancelled',
                },
            ),
            (AgentSessionActivityStatus.SUCCEEDED, {'call_id': 'missing-fields'}),
            (
                AgentSessionActivityStatus.SUCCEEDED,
                {
                    'call_id': 'missing-result',
                    'instance_id': 'clock',
                    'function': 'now',
                    'arguments': {},
                },
            ),
        ]
        for status, details in cases:
            self._activity(
                session,
                kind=AgentSessionActivityKind.TOOL,
                status=status,
                details=details,
            )

        self.assertEqual(rebuild_messages(session, system_prompt='sys'), [{'role': 'system', 'content': 'sys'}])

    def test_canonical_helper_enforces_immutable_sequence_order(self) -> None:
        class Activity:
            """Minimal activity-shaped fixture for shuffled helper input."""

            def __init__(self, seq: int, content: str) -> None:
                self.seq = seq
                self.kind = AgentSessionActivityKind.OUTPUT
                self.status = AgentSessionActivityStatus.SUCCEEDED
                self.details = {'content': content}

        messages = rebuild_messages_from_activities(
            [Activity(3, 'third'), Activity(1, 'first'), Activity(2, 'second')],
            system_prompt='sys',
        )

        self.assertEqual([message['content'] for message in messages], ['sys', 'first', 'second', 'third'])

    def test_malformed_or_non_terminal_messages_are_skipped(self) -> None:
        class Activity:
            """Minimal fixture allowing intentionally invalid message details."""

            def __init__(self, seq: int, kind: str, status: str, details: Any) -> None:
                self.seq = seq
                self.kind = kind
                self.status = status
                self.details = details

        activities = [
            Activity(
                1,
                AgentSessionActivityKind.INPUT,
                AgentSessionActivityStatus.SUCCEEDED,
                {'content': 'valid input'},
            ),
            Activity(2, AgentSessionActivityKind.INPUT, AgentSessionActivityStatus.RUNNING, {'content': 'running'}),
            Activity(3, AgentSessionActivityKind.INPUT, AgentSessionActivityStatus.FAILED, {'content': 'failed'}),
            Activity(
                4,
                AgentSessionActivityKind.INPUT,
                AgentSessionActivityStatus.SUCCEEDED,
                ['not', 'a', 'dict'],
            ),
            Activity(5, AgentSessionActivityKind.INPUT, AgentSessionActivityStatus.SUCCEEDED, {'content': 123}),
            Activity(
                6,
                AgentSessionActivityKind.OUTPUT,
                AgentSessionActivityStatus.SUCCEEDED,
                {'content': 'valid output'},
            ),
            Activity(
                7,
                AgentSessionActivityKind.OUTPUT,
                AgentSessionActivityStatus.CANCELLED,
                {'content': 'cancelled'},
            ),
            Activity(8, AgentSessionActivityKind.OUTPUT, AgentSessionActivityStatus.SUCCEEDED, {}),
        ]

        messages = rebuild_messages_from_activities(activities, system_prompt='sys')

        self.assertEqual(
            messages,
            [
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'valid input'},
                {'role': 'assistant', 'content': 'valid output'},
            ],
        )

    def test_failure_and_restart_omitted(self) -> None:
        session = make_test_session('other-agent')
        self._activity(session, kind=AgentSessionActivityKind.INPUT, details={'content': 'go'})
        self._activity(
            session,
            kind=AgentSessionActivityKind.FAILURE,
            status=AgentSessionActivityStatus.FAILED,
            details={'message': 'boom'},
        )
        self._activity(session, kind=AgentSessionActivityKind.RESTART, details={})
        self._activity(session, kind=AgentSessionActivityKind.OUTPUT, details={'content': 'ok'})

        messages = rebuild_messages(session, system_prompt='sys')
        roles = [m['role'] for m in messages]
        self.assertEqual(roles, ['system', 'user', 'assistant'])
