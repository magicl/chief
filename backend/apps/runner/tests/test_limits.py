# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for session limit enforcement (iteration, spend, and rolling budget caps)."""

from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.errors import SessionFailure
from apps.runner.limits import SessionLimitChecker
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


def _base_spec(**overrides: object) -> AgentConfigSpec:
    """Minimal spec for limit-checker unit tests."""
    defaults: dict[str, Any] = {
        'llm': LLMSpec(provider='openai', model='gpt-5.4-mini'),
        'system_prompt': 'hello',
        'tools': [ToolInstance(id='clock', type='clock')],
    }
    defaults.update(overrides)
    return AgentConfigSpec(**defaults)


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
        backend = MemorySessionBackend(spec, user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
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
        backend = MemorySessionBackend(spec, user_id=1)
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
        backend = MemorySessionBackend(spec, user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'go'})
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


class TestRollingBudgetCodes(OTestCase):
    """Verify that rolling budget breaches raise the correct specific failure code."""

    def _checker_with_budget(self, **limits: Decimal | None) -> SessionLimitChecker:
        """Build a limit checker with rolling budget limits and zero HourlyUsage baseline."""
        agent_id = uuid4()
        with (
            patch('apps.runner.limits.agent_daily_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.agent_monthly_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_daily_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_monthly_spend', return_value=Decimal(0)),
            self.settings(
                DEFAULT_MAX_SESSION_ITERATIONS=None,
                DEFAULT_MAX_SESSION_COST_USD=None,
            ),
        ):
            return SessionLimitChecker(
                _base_spec(),
                agent_id=agent_id,
                user_id=1,
                agent_daily_limit=limits.get('agent_daily'),
                agent_monthly_limit=limits.get('agent_monthly'),
                user_daily_limit=limits.get('user_daily'),
                user_monthly_limit=limits.get('user_monthly'),
            )

    def test_agent_daily_breach_code(self) -> None:
        checker = self._checker_with_budget(agent_daily=Decimal('1.00'))
        checker.record_cost(Decimal('1.00'))
        with self.assertRaises(SessionFailure) as ctx:
            checker.check()
        self.assertEqual(ctx.exception.code, 'agent_daily_spend_limit')

    def test_agent_monthly_breach_code(self) -> None:
        checker = self._checker_with_budget(agent_monthly=Decimal('5.00'))
        checker.record_cost(Decimal('5.00'))
        with self.assertRaises(SessionFailure) as ctx:
            checker.check()
        self.assertEqual(ctx.exception.code, 'agent_monthly_spend_limit')

    def test_user_daily_breach_code(self) -> None:
        checker = self._checker_with_budget(user_daily=Decimal('2.00'))
        checker.record_cost(Decimal('2.00'))
        with self.assertRaises(SessionFailure) as ctx:
            checker.check()
        self.assertEqual(ctx.exception.code, 'user_daily_spend_limit')

    def test_user_monthly_breach_code(self) -> None:
        checker = self._checker_with_budget(user_monthly=Decimal('10.00'))
        checker.record_cost(Decimal('10.00'))
        with self.assertRaises(SessionFailure) as ctx:
            checker.check()
        self.assertEqual(ctx.exception.code, 'user_monthly_spend_limit')

    def test_tightest_budget_wins(self) -> None:
        """When multiple rolling limits are set, the tightest one triggers first."""
        checker = self._checker_with_budget(
            agent_daily=Decimal('1.00'),
            agent_monthly=Decimal('50.00'),
            user_daily=Decimal('100.00'),
        )
        checker.record_cost(Decimal('1.00'))
        with self.assertRaises(SessionFailure) as ctx:
            checker.check()
        self.assertEqual(ctx.exception.code, 'agent_daily_spend_limit')


class TestDoubleCountPrevention(OTestCase):
    """Verify that refresh subtracts session_cost_usd from the HourlyUsage baseline."""

    def test_no_double_count_after_aggregation(self) -> None:
        """After aggregation folds session cost into HourlyUsage, remaining budget stays correct."""
        agent_id = uuid4()
        session_cost = Decimal('2.00')
        agent_daily_limit = Decimal('10.00')
        others_spend = Decimal('3.00')
        # After aggregation the baseline includes our session cost
        aggregated_baseline = others_spend + session_cost

        with (
            patch('apps.runner.limits.agent_daily_spend', return_value=aggregated_baseline),
            patch('apps.runner.limits.agent_monthly_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_daily_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_monthly_spend', return_value=Decimal(0)),
            self.settings(
                DEFAULT_MAX_SESSION_ITERATIONS=None,
                DEFAULT_MAX_SESSION_COST_USD=None,
            ),
        ):
            checker = SessionLimitChecker(
                _base_spec(),
                agent_id=agent_id,
                user_id=1,
                agent_daily_limit=agent_daily_limit,
            )
            # Simulate cost already recorded in this session
            checker.session_cost_usd = session_cost
            # Force a refresh (as if 5 min elapsed)
            checker._refresh_budget_levels()  # pylint: disable=protected-access

        # remaining = limit - others = 10 - 3 = 7; session spent 2 < 7 → no breach
        checker.check()

    def test_double_count_would_breach_without_fix(self) -> None:
        """Demonstrates the scenario that would falsely breach without the deduction fix."""
        agent_id = uuid4()
        session_cost = Decimal('4.00')
        agent_daily_limit = Decimal('10.00')
        others_spend = Decimal('3.00')
        aggregated_baseline = others_spend + session_cost  # 7.00

        with (
            patch('apps.runner.limits.agent_daily_spend', return_value=aggregated_baseline),
            patch('apps.runner.limits.agent_monthly_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_daily_spend', return_value=Decimal(0)),
            patch('apps.runner.limits.user_monthly_spend', return_value=Decimal(0)),
            self.settings(
                DEFAULT_MAX_SESSION_ITERATIONS=None,
                DEFAULT_MAX_SESSION_COST_USD=None,
            ),
        ):
            checker = SessionLimitChecker(
                _base_spec(),
                agent_id=agent_id,
                user_id=1,
                agent_daily_limit=agent_daily_limit,
            )
            checker.session_cost_usd = session_cost
            checker._refresh_budget_levels()  # pylint: disable=protected-access

        # remaining = limit - others = 10 - 3 = 7; session spent 4 < 7 → still OK
        checker.check()
