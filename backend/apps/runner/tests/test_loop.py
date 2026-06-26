# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import patch

from apps.agents.hardcoded import HARDCODED_SPEC
from apps.agents.spec import LLMSpec
from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.loop import SessionRunner
from apps.runner.providers.base import ProviderError, StreamResult
from apps.runner.providers.fake_provider import FakeProvider
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus

from olib.py.django.test.cases import OTestCase


class TestSessionRunner(OTestCase):
    def _backend(self, *, llm: LLMSpec | None = None) -> MemorySessionBackend:
        spec = HARDCODED_SPEC.model_copy()
        if llm is not None:
            spec.llm = llm
        return MemorySessionBackend(spec)

    def test_run_waits_without_user_input(self) -> None:
        backend = self._backend()
        runner = SessionRunner(backend)
        with patch('apps.runner.loop.make_provider') as mock_make:
            runner.run()
        mock_make.assert_not_called()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [event.kind for event in backend.events()]
        self.assertNotIn(AgentSessionEventKind.OUTPUT, kinds)
        self.assertNotIn(AgentSessionEventKind.FAILURE, kinds)

    def test_chat_input_then_response(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
        ):
            runner.run()
        kinds = [event.kind for event in backend.events()]
        self.assertIn(AgentSessionEventKind.INPUT, kinds)
        self.assertIn(AgentSessionEventKind.OUTPUT, kinds)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''}, clear=False)
    def test_missing_llm_credentials_waits_until_user_input(self) -> None:
        backend = self._backend()
        runner = SessionRunner(backend)
        with patch('apps.runner.loop.make_provider') as mock_make:
            runner.run()
        mock_make.assert_not_called()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [event.kind for event in backend.events()]
        self.assertNotIn(AgentSessionEventKind.FAILURE, kinds)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''}, clear=False)
    def test_missing_llm_credentials_records_failure_event(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        failure = next(event for event in backend.events() if event.kind == AgentSessionEventKind.FAILURE)
        self.assertEqual(failure.payload['message'], 'No OpenAI credentials specified')
        self.assertEqual(failure.payload['code'], 'missing_openai_credentials')
        self.assertNotIn('traceback', failure.payload)

    def test_provider_error_records_failure_event(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        error_result = StreamResult(error=ProviderError(message='Provider unavailable', code='provider_failure'))
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([error_result])):
            runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [event.kind for event in backend.events()]
        self.assertIn(AgentSessionEventKind.FAILURE, kinds)

    def test_unsupported_provider_records_failure_event(self) -> None:
        backend = self._backend(llm=LLMSpec(provider='unknown-provider', model='x'))
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        failure = next(event for event in backend.events() if event.kind == AgentSessionEventKind.FAILURE)
        self.assertEqual(failure.payload['code'], 'unsupported_llm_provider')
