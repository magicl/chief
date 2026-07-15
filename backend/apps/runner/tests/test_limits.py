# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for session limit enforcement (iteration and spend caps)."""

from decimal import Decimal
from unittest.mock import patch

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.loop import SessionRunner
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from libs.agent_spec import AgentConfigSpec, LLMSpec, SessionLimitsSpec, ToolInstance
from libs.providers.llm.base import StreamResult, Usage
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase

_TOOL_CALL = {'name': 'clock__now', 'arguments': {}, 'id': 'tc1'}


def _tool_response(content: str = 'ok', **kwargs: object) -> StreamResult:
    """Build a response that includes a tool call so the loop continues iterating."""
    return StreamResult(
        content=content,
        tool_calls=[_TOOL_CALL],
        usage=Usage(model='fake', input_tokens=10, output_tokens=10),
        **kwargs,  # type: ignore[arg-type]
    )


class TestSessionIterationLimit(OTestCase):
    def _spec_with_limits(self, max_iterations: int = 3) -> AgentConfigSpec:
        """Build a minimal spec with an explicit iteration cap and a tool for looping."""
        return AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits=SessionLimitsSpec(max_iterations=max_iterations),
            tools=[ToolInstance(id='clock', type='clock')],
        )

    def test_halts_at_iteration_limit(self) -> None:
        spec = self._spec_with_limits(max_iterations=2)
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        # Two tool-call responses fill the limit; third should be blocked by check()
        responses = [
            _tool_response('one'),
            _tool_response('two'),
            StreamResult(content='should not reach'),
        ]
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            with self.settings(DEFAULT_MAX_SESSION_ITERATIONS=None, DEFAULT_MAX_SESSION_COST_USD=None):
                SessionRunner(backend).run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertEqual(len(failure_events), 1)
        self.assertEqual(failure_events[0].payload['code'], 'session_iteration_limit')
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)

    def test_no_limit_allows_all_iterations(self) -> None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            tools=[ToolInstance(id='clock', type='clock')],
        )
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        responses = [
            _tool_response('one'),
            _tool_response('two'),
            StreamResult(content='done'),
        ]
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            with self.settings(DEFAULT_MAX_SESSION_ITERATIONS=None, DEFAULT_MAX_SESSION_COST_USD=None):
                SessionRunner(backend).run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertEqual(len(failure_events), 0)


class TestSessionSpendLimit(OTestCase):
    def test_halts_at_spend_limit(self) -> None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits=SessionLimitsSpec(max_cost_usd=Decimal('0.001')),
            tools=[ToolInstance(id='clock', type='clock')],
        )
        backend = MemorySessionBackend(spec)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
        # First response costs 0.01 (above 0.001 limit); second should be blocked
        responses = [
            _tool_response('expensive'),
            StreamResult(content='should not reach'),
        ]
        fake = FakeProvider.for_responses(responses)
        with (
            patch('apps.runner.loop.make_provider', return_value=fake),
            patch.object(fake, 'compute_cost_usd', return_value=Decimal('0.01')),
            self.settings(DEFAULT_MAX_SESSION_ITERATIONS=None, DEFAULT_MAX_SESSION_COST_USD=None),
        ):
            SessionRunner(backend).run()
        failure_events = [e for e in backend.events() if e.kind == AgentSessionEventKind.FAILURE]
        self.assertEqual(len(failure_events), 1)
        codes = [e.payload['code'] for e in failure_events]
        self.assertIn('session_spend_limit', codes)
