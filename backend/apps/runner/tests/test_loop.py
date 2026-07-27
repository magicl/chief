# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import asyncio
import json
import logging
from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from typing import cast
from unittest.mock import Mock, patch

from apps.runner.activity_recorder import BackendActivityRecorder
from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookSet
from apps.runner.loop import SessionRunner
from apps.sessions.models import AgentSessionActivityKind, AgentSessionStatus

# isort: split

from libs.agent_spec import LLMSpec, load_example
from libs.providers.llm.base import ProviderError, StreamResult, Usage
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestSessionRunner(OTestCase):
    def _backend(self, *, llm: LLMSpec | None = None) -> MemorySessionBackend:
        spec = load_example('clock-assistant').model_copy()
        if llm is not None:
            spec.llm = llm
        return MemorySessionBackend(spec, user_id=1)

    def test_run_waits_without_user_input(self) -> None:
        backend = self._backend()
        runner = SessionRunner(backend)
        with patch('apps.runner.loop.make_provider') as mock_make:
            runner.run()
        mock_make.assert_not_called()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [activity.kind for activity in backend.activities()]
        self.assertNotIn(AgentSessionActivityKind.OUTPUT, kinds)
        self.assertNotIn(AgentSessionActivityKind.FAILURE, kinds)

    def test_chat_input_then_response(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
        ):
            runner.run()
        kinds = [activity.kind for activity in backend.activities()]
        self.assertIn(AgentSessionActivityKind.INPUT, kinds)
        self.assertIn(AgentSessionActivityKind.OUTPUT, kinds)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''}, clear=False)
    def test_missing_llm_credentials_waits_until_user_input(self) -> None:
        backend = self._backend()
        runner = SessionRunner(backend)
        with patch('apps.runner.loop.make_provider') as mock_make:
            runner.run()
        mock_make.assert_not_called()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        kinds = [activity.kind for activity in backend.activities()]
        self.assertNotIn(AgentSessionActivityKind.FAILURE, kinds)

    @patch.dict('os.environ', {'OPENAI_API_KEY': ''}, clear=False)
    def test_missing_llm_credentials_records_failure_event(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(failure.details['message'], 'No OpenAI credentials specified')
        self.assertEqual(failure.details['code'], 'missing_openai_credentials')
        self.assertNotIn('traceback', failure.details)

    def test_provider_error_records_failure_event(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        secret = 'Authorization: Bearer provider-secret-value'
        error_result = StreamResult(error=ProviderError(message=secret, code='provider_failure'))
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([error_result])):
            runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        activities = backend.activities()
        kinds = [activity.kind for activity in activities]
        self.assertIn(AgentSessionActivityKind.FAILURE, kinds)
        failure = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.FAILURE)
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(failure.details, {'message': 'Provider request failed', 'code': 'provider_failure'})
        self.assertEqual(llm.details, {'message': 'Provider request failed', 'code': 'provider_failure'})
        self.assertNotIn(secret, json.dumps([activity.to_stream_dict() for activity in activities], default=str))
        self.assertNotIn(secret, json.dumps(backend.published_activities))

    def test_credential_storage_misconfigured_records_distinct_failure(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        error_result = StreamResult(
            error=ProviderError(message='credential storage misconfigured', code='credential_storage_misconfigured'),
        )
        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([error_result])):
            runner.run()
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(failure.details['code'], 'credential_storage_misconfigured')
        self.assertEqual(failure.details['message'], 'credential storage misconfigured')
        self.assertNotIn('traceback', failure.details)

    def test_unsupported_provider_records_failure_event(self) -> None:
        backend = self._backend(llm=LLMSpec(provider='unknown-provider', model='x'))
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        runner.run()
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(failure.details['code'], 'unsupported_llm_provider')

    def test_backend_always_wires_supplier_to_provider_config(self) -> None:
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        with patch('apps.runner.loop.make_provider') as mock_make:
            mock_make.return_value = FakeProvider.for_responses([StreamResult(content='pong')])
            SessionRunner(backend).run()
        cfg = mock_make.call_args[0][0]
        self.assertEqual(cfg.user_id, 1)
        self.assertIsNotNone(cfg.secret_supplier)

    def test_tool_call_invokes_bound_instance(self) -> None:
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': '1'}])
        follow_up = StreamResult(content='done')
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([tool_call, follow_up]),
        ):
            SessionRunner(backend).run()
        tools = [activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL]
        self.assertTrue(any('T' in activity.details.get('result', '') for activity in tools))

    def test_tool_is_one_running_then_succeeded_activity(self) -> None:
        """A tool call updates one published activity instead of adding a result row."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        responses = [
            StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}]),
            StreamResult(content='done'),
        ]

        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            SessionRunner(backend).run()

        tools = [activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].status, 'succeeded')
        self.assertGreaterEqual(tools[0].revision, 2)
        self.assertIn('result', tools[0].details)
        published = [item for item in backend.published_activities if item['id'] == str(tools[0].id)]
        self.assertEqual([item['status'] for item in published], ['running', 'succeeded'])
        self.assertEqual(
            {activity.kind for activity in backend.activities()},
            {'input', 'llm', 'output', 'tool'},
        )

    def test_output_and_tool_are_children_of_their_llm(self) -> None:
        """Each provider collection owns its generated output and requested tools."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        responses = [
            StreamResult(
                content='checking',
                tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}],
                usage=Usage(model='fake-model', input_tokens=10, output_tokens=2),
                latency_ms=7,
            ),
            StreamResult(content='done'),
        ]

        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            SessionRunner(backend).run()

        llms = [activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM]
        outputs = [activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.OUTPUT]
        tool = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL)
        self.assertEqual(len(llms), 2)
        self.assertEqual(tool.parent_id, llms[0].id)
        self.assertEqual(outputs[0].parent_id, llms[0].id)
        self.assertEqual(outputs[1].parent_id, llms[1].id)
        self.assertEqual(llms[0].model, 'fake-model')
        self.assertEqual(llms[0].input_tokens, 10)
        self.assertEqual(llms[0].output_tokens, 2)
        self.assertEqual(llms[0].latency_ms, 7)
        for output in outputs:
            self.assertIsNone(output.model)
            self.assertIsNone(output.input_tokens)
            self.assertIsNone(output.output_tokens)
            self.assertIsNone(output.cost_usd)
            self.assertIsNone(output.latency_ms)

    def test_denied_unknown_and_raised_tools_finish_failed(self) -> None:
        """Every uniform tool failure updates its running activity to failed."""
        cases = [
            ('clock__tomorrow', None, 'Permission denied'),
            ('missing__now', None, 'Unknown tool instance'),
            ('clock__now', RuntimeError('clock broke'), 'tool_execution_failed'),
        ]
        for qualified_name, raised, expected_failure in cases:
            with self.subTest(qualified_name=qualified_name, raised=raised is not None):
                backend = self._backend()
                backend.push_mailbox({'action': 'chat', 'content': 'time?'})
                responses = [
                    StreamResult(tool_calls=[{'name': qualified_name, 'arguments': {'zone': 'UTC'}, 'id': 'call-1'}]),
                    StreamResult(content='done'),
                ]
                runner = SessionRunner(backend)
                if raised is not None:
                    bound = runner.bound_tools['clock']
                    runner.bound_tools['clock'] = replace(bound, invoke=Mock(side_effect=raised))

                with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
                    runner.run()

                tool = next(
                    activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL
                )
                self.assertEqual(tool.status, 'failed')
                self.assertGreaterEqual(tool.revision, 2)
                parsed_result = json.loads(tool.details['result'])
                self.assertTrue('failure' in parsed_result or parsed_result.get('ok') is False)
                self.assertIn(expected_failure, tool.details['result'])
                self.assertIsNotNone(tool.latency_ms)
                self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

    def test_provider_failure_finishes_llm_before_terminal_failure(self) -> None:
        """A provider result failure closes its LLM before recording session failure."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(error=ProviderError(message='Provider unavailable', code='provider_failure'))

        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses([result])):
            SessionRunner(backend).run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(llm.status, 'failed')
        self.assertLess(llm.seq, failure.seq)
        self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

    def test_provider_failure_preserves_available_usage_cost_and_latency(self) -> None:
        """A provider failure closes its LLM with all returned billing metadata."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(
            error=ProviderError(message='Provider unavailable', code='provider_failure'),
            usage=Usage(model='failed-model', input_tokens=11, output_tokens=3),
            latency_ms=47,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(return_value=Decimal('0.004200'))  # type: ignore[method-assign]
        runner = SessionRunner(backend)

        with patch('apps.runner.loop.make_provider', return_value=provider):
            runner.run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'failed-model')
        self.assertEqual(llm.input_tokens, 11)
        self.assertEqual(llm.output_tokens, 3)
        self.assertEqual(llm.cost_usd, Decimal('0.004200'))
        self.assertEqual(llm.latency_ms, 47)
        self.assertEqual(
            runner._limit_checker.session_cost_usd,  # pylint: disable=protected-access
            Decimal('0.004200'),
        )
        self.assertLess(llm.seq, failure.seq)

    def test_provider_failure_cost_calculation_raise_still_closes_lifecycle(self) -> None:
        """Billing instrumentation cannot strand an LLM returned as failed."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(
            error=ProviderError(message='Provider unavailable', code='provider_failure'),
            usage=Usage(model='failed-model', input_tokens=11, output_tokens=3),
            latency_ms=47,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(side_effect=RuntimeError('pricing unavailable'))  # type: ignore[method-assign]

        with patch('apps.runner.loop.make_provider', return_value=provider):
            SessionRunner(backend).run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        failure = next(
            activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.FAILURE
        )
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'failed-model')
        self.assertEqual(llm.input_tokens, 11)
        self.assertEqual(llm.output_tokens, 3)
        self.assertIsNone(llm.cost_usd)
        self.assertEqual(llm.latency_ms, 47)
        self.assertLess(llm.seq, failure.seq)

    def test_provider_failure_cost_accounting_raise_still_records_failure(self) -> None:
        """Accounting faults cannot suppress provider or LLM failure records."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(
            error=ProviderError(message='Provider unavailable', code='provider_failure'),
            usage=Usage(model='failed-model', input_tokens=11, output_tokens=3),
            latency_ms=47,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(return_value=Decimal('0.004200'))  # type: ignore[method-assign]
        runner = SessionRunner(backend)
        record_cost = Mock(side_effect=RuntimeError('accounting unavailable'))
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access

        with patch('apps.runner.loop.make_provider', return_value=provider):
            runner.run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        failure = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.FAILURE)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.cost_usd, Decimal('0.004200'))
        self.assertLess(llm.seq, failure.seq)
        record_cost.assert_called_once_with(Decimal('0.004200'))
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_successful_result_pricing_raise_preserves_failed_llm_metadata(self) -> None:
        """A success-path pricing fault closes the LLM with available metadata."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(
            content='pong',
            usage=Usage(model='priced-model', input_tokens=13, output_tokens=5),
            latency_ms=61,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(side_effect=RuntimeError('pricing unavailable'))  # type: ignore[method-assign]
        runner = SessionRunner(backend)

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            expectLogItems(
                [
                    ExpectLogItem(
                        'apps.runner.loop',
                        logging.ERROR,
                        r'Session .* unexpected failure',
                        count=1,
                    )
                ]
            ),
        ):
            runner.run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'priced-model')
        self.assertEqual(llm.input_tokens, 13)
        self.assertEqual(llm.output_tokens, 5)
        self.assertIsNone(llm.cost_usd)
        self.assertEqual(llm.latency_ms, 61)
        self.assertTrue(any(activity.kind == AgentSessionActivityKind.FAILURE for activity in activities))
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_uniform_tool_failure_result_finishes_failed(self) -> None:
        """A tool-returned uniform failure envelope marks its lifecycle failed."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        runner = SessionRunner(backend)
        bound = runner.bound_tools['clock']
        runner.bound_tools['clock'] = replace(bound, invoke=Mock(return_value={'ok': False, 'failure': 'no clock'}))
        responses = [
            StreamResult(tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}]),
            StreamResult(content='done'),
        ]

        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            runner.run()

        tool = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL)
        self.assertEqual(tool.status, 'failed')
        self.assertIn('no clock', tool.details['result'])
        self.assertIsNotNone(tool.latency_ms)

    def test_provider_cancellation_terminalizes_llm_and_reraises(self) -> None:
        """Provider cancellation fails the open LLM without becoming session failure."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        provider = FakeProvider.for_responses([])
        provider.collect = Mock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
        runner = SessionRunner(backend)
        record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError),
        ):
            runner.run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        record_cost.assert_not_called()
        self.assertFalse(any(activity.kind == AgentSessionActivityKind.FAILURE for activity in activities))
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_llm_start_hook_cancellation_terminalizes_created_activity(self) -> None:
        """A hook BaseException after persistence cannot strand the created LLM."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)

        def cancel_llm_start(activity: object) -> None:
            """Cancel only after the LLM activity has been persisted."""
            if getattr(activity, 'kind', None) == AgentSessionActivityKind.LLM:
                raise asyncio.CancelledError

        runner.add_hook(HookSet(on_activity_created=cancel_llm_start))
        with self.assertRaises(asyncio.CancelledError):
            runner.run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_metadata_extraction_cancellation_terminalizes_llm_and_reraises(self) -> None:
        """Metadata extraction cancellation closes the LLM with partial metadata."""

        class MetadataCancelledResult:
            """Provider result whose usage extraction is cancelled."""

            content = 'pong'
            tool_calls: list[dict[str, object]] = []
            error = None
            latency_ms = 37

            @property
            def usage(self) -> Usage:
                """Cancel while reading provider usage."""
                raise asyncio.CancelledError

        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = cast(StreamResult, MetadataCancelledResult())
        provider = FakeProvider.for_responses([result])

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError),
        ):
            SessionRunner(backend).run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'gpt-5.4-mini')
        self.assertEqual(llm.latency_ms, 37)
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_result_status_cancellation_terminalizes_llm_and_reraises(self) -> None:
        """Cancellation while reading result status cannot strand the LLM."""

        class StatusCancelledResult:
            """Provider result whose failure-status extraction is cancelled."""

            content = 'pong'
            tool_calls: list[dict[str, object]] = []
            usage = Usage(model='status-model', input_tokens=9, output_tokens=4)
            latency_ms = 43

            @property
            def error(self) -> ProviderError | None:
                """Cancel while reading provider result status."""
                raise asyncio.CancelledError

        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = cast(StreamResult, StatusCancelledResult())
        provider = FakeProvider.for_responses([result])

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError),
        ):
            SessionRunner(backend).run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'status-model')
        self.assertEqual(llm.latency_ms, 43)
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_pricing_cancellation_terminalizes_llm_and_reraises(self) -> None:
        """Pricing cancellation closes the LLM with available usage metadata."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        result = StreamResult(
            content='pong',
            usage=Usage(model='priced-model', input_tokens=13, output_tokens=5),
            latency_ms=61,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError),
        ):
            SessionRunner(backend).run()

        activities = backend.activities()
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'priced-model')
        self.assertEqual(llm.input_tokens, 13)
        self.assertEqual(llm.output_tokens, 5)
        self.assertEqual(llm.latency_ms, 61)
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_success_update_hook_cancellation_keeps_terminal_llm_and_cost(self) -> None:
        """A success update hook cancellation cannot re-fail the persisted LLM."""
        cancellation = asyncio.CancelledError('success update cancelled')
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        result = StreamResult(
            content='pong',
            usage=Usage(model='billed-model', input_tokens=7, output_tokens=2),
            latency_ms=19,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(return_value=Decimal('0.003100'))  # type: ignore[method-assign]
        record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access

        def cancel_success_update(activity: object) -> None:
            """Cancel when the LLM success transition is published."""
            if (
                getattr(activity, 'kind', None) == AgentSessionActivityKind.LLM
                and getattr(activity, 'status', None) == 'succeeded'
            ):
                raise cancellation

        runner.add_hook(HookSet(on_activity_updated=cancel_success_update))
        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            runner.run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(llm.status, 'succeeded')
        record_cost.assert_called_once_with(Decimal('0.003100'))
        self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

    def test_failure_update_hook_cancellation_keeps_terminal_llm_and_cost(self) -> None:
        """A failure update hook cancellation cannot mutate the failed LLM twice."""
        cancellation = asyncio.CancelledError('failure update cancelled')
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        runner._emit_output = Mock(  # type: ignore[method-assign]  # pylint: disable=protected-access
            side_effect=RuntimeError('output persistence failed')
        )
        result = StreamResult(
            content='pong',
            usage=Usage(model='billed-model', input_tokens=7, output_tokens=2),
            latency_ms=19,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(return_value=Decimal('0.003100'))  # type: ignore[method-assign]
        record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access

        def cancel_failure_update(activity: object) -> None:
            """Cancel when the LLM failure transition is published."""
            if (
                getattr(activity, 'kind', None) == AgentSessionActivityKind.LLM
                and getattr(activity, 'status', None) == 'failed'
            ):
                raise cancellation

        runner.add_hook(HookSet(on_activity_updated=cancel_failure_update))
        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            runner.run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(llm.status, 'failed')
        record_cost.assert_called_once_with(Decimal('0.003100'))
        self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

    def test_generate_end_hook_cancellation_closes_llm_and_accounts_cost(self) -> None:
        """Generate-end cancellation occurs after metadata and cost accounting."""
        cancellation = asyncio.CancelledError('generate end cancelled')
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        result = StreamResult(
            content='pong',
            usage=Usage(model='billed-model', input_tokens=7, output_tokens=2),
            latency_ms=19,
        )
        provider = FakeProvider.for_responses([result])
        provider.compute_cost_usd = Mock(return_value=Decimal('0.003100'))  # type: ignore[method-assign]
        record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access
        runner.add_hook(HookSet(on_generate_end=Mock(side_effect=cancellation)))

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            runner.run()

        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(llm.model, 'billed-model')
        self.assertEqual(llm.cost_usd, Decimal('0.003100'))
        record_cost.assert_called_once_with(Decimal('0.003100'))
        self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

    def test_tool_cancellation_terminalizes_tool_and_llm_then_reraises(self) -> None:
        """Tool cancellation fails both open lifecycles without session failure."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        runner = SessionRunner(backend)
        bound = runner.bound_tools['clock']
        runner.bound_tools['clock'] = replace(bound, invoke=Mock(side_effect=asyncio.CancelledError()))
        provider = FakeProvider.for_responses(
            [
                StreamResult(
                    tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}],
                    usage=Usage(model='billed-model', input_tokens=7, output_tokens=2),
                    latency_ms=19,
                )
            ]
        )
        provider.compute_cost_usd = Mock(return_value=Decimal('0.003100'))  # type: ignore[method-assign]
        record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
        runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            self.assertRaises(asyncio.CancelledError),
        ):
            runner.run()

        activities = backend.activities()
        tool = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.TOOL)
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(tool.status, 'failed')
        self.assertEqual(llm.status, 'failed')
        record_cost.assert_called_once_with(Decimal('0.003100'))
        self.assertFalse(any(activity.kind == AgentSessionActivityKind.FAILURE for activity in activities))
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_success_and_processing_failure_account_known_cost_once(self) -> None:
        """Collected responses account known cost once across terminal paths."""
        for processing_failure in (False, True):
            with self.subTest(processing_failure=processing_failure):
                backend = self._backend()
                backend.push_mailbox({'action': 'chat', 'content': 'ping'})
                runner = SessionRunner(backend)
                result = StreamResult(
                    content='pong',
                    usage=Usage(model='billed-model', input_tokens=7, output_tokens=2),
                    latency_ms=19,
                )
                provider = FakeProvider.for_responses([result])
                provider.compute_cost_usd = Mock(return_value=Decimal('0.003100'))  # type: ignore[method-assign]
                record_cost = Mock(wraps=runner._limit_checker.record_cost)  # pylint: disable=protected-access
                runner._limit_checker.record_cost = record_cost  # type: ignore[method-assign]  # pylint: disable=protected-access
                if processing_failure:
                    runner._emit_output = Mock(  # type: ignore[method-assign]  # pylint: disable=protected-access
                        side_effect=RuntimeError('output persistence failed')
                    )

                context = (
                    expectLogItems(
                        [
                            ExpectLogItem(
                                'apps.runner.loop',
                                logging.ERROR,
                                r'Session .* unexpected failure',
                                count=1,
                            )
                        ]
                    )
                    if processing_failure
                    else nullcontext()
                )
                with patch('apps.runner.loop.make_provider', return_value=provider), context:
                    runner.run()

                record_cost.assert_called_once_with(Decimal('0.003100'))
                self.assertEqual(
                    runner._limit_checker.session_cost_usd,  # pylint: disable=protected-access
                    Decimal('0.003100'),
                )

    def test_unexpected_tool_raise_hides_secret_from_activity_and_provider(self) -> None:
        """Unexpected tool failures expose only a stable model-visible result."""
        secret = 'api_key=super-secret-value'
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        runner = SessionRunner(backend)
        bound = runner.bound_tools['clock']
        runner.bound_tools['clock'] = replace(bound, invoke=Mock(side_effect=RuntimeError(secret)))
        provider = FakeProvider.for_responses(
            [
                StreamResult(tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}]),
                StreamResult(content='done'),
            ]
        )
        provider.collect = Mock(wraps=provider.collect)  # type: ignore[method-assign]

        with patch('apps.runner.loop.make_provider', return_value=provider):
            runner.run()

        serialized_activities = json.dumps(
            [activity.to_stream_dict() for activity in backend.activities()],
            default=str,
        )
        serialized_publications = json.dumps(backend.published_activities)
        tool_message = provider.collect.call_args_list[1].args[0][-1]
        expected_result = json.dumps(
            {
                'ok': False,
                'error': {
                    'code': 'tool_execution_failed',
                    'message': 'Tool execution failed',
                },
            }
        )
        self.assertNotIn(secret, serialized_activities)
        self.assertNotIn(secret, serialized_publications)
        self.assertEqual(tool_message['content'], expected_result)

    def test_raised_provider_call_leaves_no_running_activity(self) -> None:
        """A provider runtime raise closes the LLM and records a terminal failure."""
        secret = 'https://user:provider-secret@example.invalid/path?token=hidden'
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        provider = FakeProvider.for_responses([])
        provider.collect = Mock(side_effect=RuntimeError(secret))  # type: ignore[method-assign]

        with (
            patch('apps.runner.loop.make_provider', return_value=provider),
            expectLogItems(
                [
                    ExpectLogItem(
                        'apps.runner.loop',
                        logging.ERROR,
                        r'Session .* unexpected failure',
                        count=1,
                    )
                ]
            ),
        ):
            SessionRunner(backend).run()

        activities = backend.activities()
        failure = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.FAILURE)
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(failure.details, {'message': 'Unexpected session failure', 'code': 'unexpected_failure'})
        self.assertEqual(llm.details, {'message': 'Provider request failed', 'code': 'provider_runtime_failure'})
        self.assertNotIn(secret, json.dumps([activity.to_stream_dict() for activity in activities], default=str))
        self.assertNotIn(secret, json.dumps(backend.published_activities))
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_tool_recorder_span_nests_under_current_tool(self) -> None:
        """Tool-internal recorder work inherits the running tool as parent."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        runner = SessionRunner(backend)
        bound = runner.bound_tools['clock']

        def invoke_with_span(function_name: str, arguments: dict[str, object]) -> str:
            """Simulate a tool that records nested internal work."""
            del function_name, arguments
            with runner.ctx.recorder.span(name='inside-tool', summary='Nested work'):
                return 'nested result'

        runner.bound_tools['clock'] = replace(bound, invoke=invoke_with_span)
        responses = [
            StreamResult(tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}]),
            StreamResult(content='done'),
        ]

        with patch('apps.runner.loop.make_provider', return_value=FakeProvider.for_responses(responses)):
            runner.run()

        tool = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.TOOL)
        span = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.SPAN)
        self.assertIsInstance(runner.ctx.recorder, BackendActivityRecorder)
        self.assertEqual(span.parent_id, tool.id)
        self.assertEqual(span.status, 'succeeded')

    def test_tool_turn_rebuilds_provider_messages_in_wire_order(self) -> None:
        """A follow-up collection receives output, tool call, then tool result messages."""
        backend = self._backend()
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        provider = FakeProvider.for_responses(
            [
                StreamResult(
                    content='checking',
                    tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'call-1'}],
                ),
                StreamResult(content='done'),
            ]
        )
        provider.collect = Mock(wraps=provider.collect)  # type: ignore[method-assign]

        with patch('apps.runner.loop.make_provider', return_value=provider):
            SessionRunner(backend).run()

        second_messages = provider.collect.call_args_list[1].args[0]
        self.assertEqual([message['role'] for message in second_messages], ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(second_messages[-1]['tool_call_id'], 'call-1')

    def test_loop_passes_llm_credential_ref_to_provider_config(self) -> None:
        llm = LLMSpec(provider='openai', model='gpt-5.4-mini', credential_ref='my-openai')
        backend = self._backend(llm=llm)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        with patch('apps.runner.loop.make_provider') as mock_make:
            mock_make.return_value = FakeProvider.for_responses([StreamResult(content='pong')])
            SessionRunner(backend).run()
        cfg = mock_make.call_args[0][0]
        self.assertEqual(cfg.credential_ref, 'my-openai')
