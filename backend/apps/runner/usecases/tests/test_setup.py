# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Usecase setup helper smoke tests."""

import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.runner.backends.base import RecordedEvent
from apps.runner.usecases.setup import build_memory_session_runner
from apps.sessions.models import AgentSessionEventKind

# isort: split

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.gmail.mock import MockGmailClient
from libs.providers.llm.base import StreamResult
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase
from olib.py.eval import EventLogWriter, RunPartition


class TestUsecaseSetup(OTestCase):
    def test_memory_runner_writes_observability_log(self) -> None:
        """Helper wires MemorySessionBackend, mock Gmail, FakeProvider, and event log hooks."""
        events, log_text, _stdout = self._run_memory_runner_smoke()

        self.assertIn(AgentSessionEventKind.TOOL_RESULT, [event.kind for event in events])
        self.assertIn('"event": "session_event"', log_text)

    def test_memory_runner_keeps_stdout_quiet(self) -> None:
        """Default observability sinks use logging so runner progress does not print to stdout."""
        _events, _log_text, stdout = self._run_memory_runner_smoke()

        self.assertNotIn('[generate]', stdout)
        self.assertNotIn('[event]', stdout)
        self.assertNotIn('[tool]', stdout)

    def _run_memory_runner_smoke(self) -> tuple[list[RecordedEvent], str, str]:
        """Run the gmail list smoke scenario; return events, JSONL text, and captured stdout."""
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='Triage inbox messages.',
            tools=[ToolInstance(id='gmail', type='gmail', allow=['list'], config={'subject': 'me@example.com'})],
        )
        gmail_client = MockGmailClient(token_supplier=lambda: None, config={'subject': 'me@example.com'})
        gmail_client.seed_message('msg-1', labels=('INBOX',), message={'subject': 'Hello'})

        with TemporaryDirectory() as temp_dir:
            partition = RunPartition(
                kind='functional', suite='runner', sample_id='gmail-smoke', model='fake', run_id='r1'
            )
            log_writer = EventLogWriter(Path(temp_dir))
            backend, runner = build_memory_session_runner(
                spec=spec,
                client_factories={'gmail': lambda **_kwargs: gmail_client},
                partition=partition,
                log_writer=log_writer,
                prompt='Check my inbox.',
            )
            tool_call = StreamResult(
                content='',
                tool_calls=[{'name': 'gmail__list', 'arguments': {'query': 'in:inbox'}, 'id': 'call-1'}],
            )

            captured = io.StringIO()
            with (
                patch(
                    'apps.runner.loop.make_provider',
                    return_value=FakeProvider.for_responses([tool_call, StreamResult(content='done')]),
                ),
                contextlib.redirect_stdout(captured),
            ):
                runner.run()

            log_path = log_writer.path_for(partition)
            log_text = log_path.read_text(encoding='utf-8') if log_path.exists() else ''
            return list(backend.events()), log_text, captured.getvalue()
