# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""SessionRunner observability hook tests."""

import logging
from typing import Any
from unittest.mock import patch

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookSet
from apps.runner.loop import SessionRunner
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from libs.agent_specs import load_example
from libs.providers.llm.base import StreamResult
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestSessionRunnerHooks(OTestCase):
    def test_hooks_fire_for_generate_tool_and_run(self) -> None:
        """Hooks observe generate, tool call, and run lifecycle without altering control flow."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy())
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'}])
        follow_up = StreamResult(content='done')
        observed: list[tuple[str, Any]] = []

        hooks = HookSet(
            on_run_start=lambda: observed.append(('run_start', None)),
            on_run_end=lambda: observed.append(('run_end', None)),
            on_generate_start=lambda messages, tool_definitions: observed.append(
                ('generate_start', (messages, tool_definitions)),
            ),
            on_generate_end=lambda result: observed.append(('generate_end', result)),
            on_tool_call_start=lambda call: observed.append(('tool_call_start', call)),
            on_tool_call_end=lambda call, result_content: observed.append(
                ('tool_call_end', (call, result_content)),
            ),
            on_event=lambda event: observed.append(('event', event.kind)),
            on_status=lambda status: observed.append(('status', status)),
        )

        runner = SessionRunner(backend)
        runner.add_hook(hooks)
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([tool_call, follow_up]),
        ):
            runner.run()

        hook_names = [name for name, _payload in observed]
        self.assertEqual(hook_names.count('run_start'), 1)
        self.assertEqual(hook_names.count('run_end'), 1)
        self.assertEqual(hook_names.count('generate_start'), 2)
        self.assertEqual(hook_names.count('generate_end'), 2)
        self.assertEqual(hook_names.count('tool_call_start'), 1)
        self.assertEqual(hook_names.count('tool_call_end'), 1)
        self.assertIn(('event', AgentSessionEventKind.INPUT), observed)
        self.assertIn(('event', AgentSessionEventKind.OUTPUT), observed)
        self.assertIn(('event', AgentSessionEventKind.TOOL_CALL), observed)
        self.assertIn(('event', AgentSessionEventKind.TOOL_RESULT), observed)
        self.assertIn(('status', AgentSessionStatus.WAITING), observed)

        tool_start = next(payload for name, payload in observed if name == 'tool_call_start')
        self.assertEqual(tool_start, {'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'})
        tool_end = next(payload for name, payload in observed if name == 'tool_call_end')
        ended_call, result_content = tool_end
        self.assertEqual(ended_call, tool_start)
        self.assertIn('T', result_content)
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        self.assertNotIn(AgentSessionEventKind.FAILURE, [event.kind for event in backend.events()])

    def test_hook_failure_does_not_fail_session(self) -> None:
        """Observability hook raises are swallowed so the session still completes."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy())
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})

        def fail_run_start() -> None:
            """Raise from a hook to prove observability failures are isolated."""
            raise RuntimeError('hook broke')

        runner = SessionRunner(backend)
        runner.add_hook(HookSet(on_run_start=fail_run_start))
        with (
            patch(
                'apps.runner.loop.make_provider',
                return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
            ),
            expectLogItems(
                [
                    ExpectLogItem(
                        'apps.runner.hooks', logging.ERROR, r'Session runner hook on_run_start failed', count=1
                    )
                ],
            ),
        ):
            runner.run()

        kinds = [event.kind for event in backend.events()]
        self.assertIn(AgentSessionEventKind.OUTPUT, kinds)
        self.assertNotIn(AgentSessionEventKind.FAILURE, kinds)
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
