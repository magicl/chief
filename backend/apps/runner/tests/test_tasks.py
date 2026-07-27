# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import asyncio
import json
import logging
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Trigger
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.loop import SessionRunner
from apps.runner.tasks import run_session
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
    TriggerType,
)
from apps.sessions.services import commands as session_commands
from apps.sessions.tests.base import make_test_session
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec
from libs.providers.llm.base import StreamResult, Usage

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestDjangoInputActivity(OTransactionTestCase):
    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_recorded_input_uses_only_activity_persistence(self, mock_schedule: Any) -> None:
        """The backend returns the canonical input without a legacy event projection."""
        session = make_test_session('input-rebuild-agent')
        backend = DjangoSessionBackend(session)

        recorded = backend.record_input('latest user input')

        self.assertEqual(
            backend.rebuild_messages(system_prompt='system'),
            [
                {'role': 'system', 'content': 'system'},
                {'role': 'user', 'content': 'latest user input'},
            ],
        )
        activity = AgentSessionActivity.objects.get(
            session=session,
            kind=AgentSessionActivityKind.INPUT,
        )
        self.assertEqual(recorded.id, activity.id)
        self.assertEqual(recorded.details, {'content': 'latest user input'})
        self.assertEqual(AgentSessionActivity.objects.filter(session=session).count(), 1)
        mock_schedule.assert_called_once_with(session.id)

    @patch('apps.sessions.services.commands._schedule_generate_session_name')
    def test_multi_turn_tool_result_is_in_second_provider_request(self, _schedule: Any) -> None:
        """The transitional Django writer keeps provider-visible activity history complete."""
        session = make_test_session('django-multi-turn')
        backend = DjangoSessionBackend(session)
        backend.record_input('What time is it?')
        provider = MagicMock()
        provider.compute_cost_usd.return_value = Decimal('0.001000')
        provider.collect.side_effect = [
            StreamResult(
                content='Let me check.',
                tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'}],
                usage=Usage(model='gpt-5.4-mini', input_tokens=10, output_tokens=2),
                latency_ms=4,
            ),
            StreamResult(
                content='It is available.',
                usage=Usage(model='gpt-5.4-mini', input_tokens=12, output_tokens=3),
                latency_ms=5,
            ),
        ]

        with (
            patch('apps.runner.backends.django.mailbox_drain', return_value=[]),
            patch('apps.runner.loop.make_provider', return_value=provider),
        ):
            SessionRunner(backend).run()

        second_messages = provider.collect.call_args_list[1].args[0]
        self.assertEqual([message['role'] for message in second_messages], ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(second_messages[2]['content'], 'Let me check.')
        self.assertEqual(second_messages[2]['tool_calls'][0]['id'], 'clock-1')
        self.assertEqual(second_messages[3]['tool_call_id'], 'clock-1')
        self.assertIn('T', second_messages[3]['content'])
        self.assertEqual(
            AgentSessionActivity.objects.filter(
                session=session,
                kind=AgentSessionActivityKind.LLM,
            ).count(),
            2,
        )
        tool = AgentSessionActivity.objects.get(
            session=session,
            kind=AgentSessionActivityKind.TOOL,
        )
        self.assertEqual(tool.status, AgentSessionActivityStatus.SUCCEEDED)


class TestRunSessionResumability(OTransactionTestCase):
    @expectLogItems(
        [
            ExpectLogItem(
                'apps.runner.tasks',
                logging.ERROR,
                r'Unhandled failure in session',
                count=1,
            )
        ]
    )
    def test_worker_failure_persists_and_publishes_canonical_activity(self) -> None:
        """Worker-boundary failures use canonical activity persistence and publication."""
        session = make_test_session('worker-failure')
        runner = MagicMock()
        secret = 'https://token:worker-secret@example.invalid/private'
        runner.run.side_effect = RuntimeError(secret)

        with (
            patch('apps.runner.tasks.try_acquire_lock', return_value=True),
            patch('apps.runner.tasks.release_lock'),
            patch('apps.runner.tasks.SessionRunner.for_session', return_value=runner),
            patch('apps.sessions.services.commands.publish_session_activity') as publish_activity,
            self.assertRaises(RuntimeError),
        ):
            run_session.run(str(session.id))

        session.refresh_from_db()
        failure = AgentSessionActivity.objects.get(
            session=session,
            kind=AgentSessionActivityKind.FAILURE,
        )
        self.assertEqual(failure.status, AgentSessionActivityStatus.FAILED)
        self.assertEqual(failure.details['code'], 'unexpected_failure')
        self.assertEqual(failure.details['message'], 'Unexpected worker failure')
        self.assertNotIn(secret, json.dumps(failure.to_stream_dict()))
        self.assertNotIn(secret, json.dumps(publish_activity.call_args.args[1]))
        self.assertNotIn('traceback', failure.details)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)
        publish_activity.assert_called_once()

    @expectLogItems(
        [
            ExpectLogItem(
                'apps.runner.tasks',
                logging.ERROR,
                r'Interrupted session',
                count=1,
            )
        ]
    )
    def test_cancellation_closes_lifecycle_fails_parent_and_propagates(self) -> None:
        """Cancellation after start cannot finalize the child or parent as successful."""
        parent = make_test_session('task-cancel-parent')
        child = session_commands.start_linked_child_session(
            parent_session=parent,
            agent=parent.agent,
            dispatch=False,
        )
        cancellation = asyncio.CancelledError('authorization=secret-cancel-value')
        runner = MagicMock()

        def start_then_cancel() -> None:
            """Create an open lifecycle before propagating task cancellation."""
            session_commands.create_activity(
                child,
                kind=AgentSessionActivityKind.LLM,
                status=AgentSessionActivityStatus.RUNNING,
                name='model',
                summary='generate',
                details={},
            )
            raise cancellation

        runner.run.side_effect = start_then_cancel
        with (
            patch('apps.runner.tasks.try_acquire_lock', return_value=True),
            patch('apps.runner.tasks.release_lock') as release,
            patch('apps.runner.tasks.SessionRunner.for_session', return_value=runner),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            run_session.run(str(child.id))

        child.refresh_from_db()
        parent_reference = AgentSessionActivity.objects.get(child_session=child)
        activities = list(child.activities.order_by('seq'))
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(child.status, AgentSessionStatus.WAITING)
        self.assertNotEqual(parent_reference.status, AgentSessionActivityStatus.SUCCEEDED)
        self.assertEqual(parent_reference.status, AgentSessionActivityStatus.FAILED)
        self.assertFalse(
            any(
                activity.status
                in {
                    AgentSessionActivityStatus.PENDING,
                    AgentSessionActivityStatus.RUNNING,
                }
                for activity in activities
            )
        )
        failure = activities[-1]
        self.assertEqual(failure.details, {'message': 'Session cancelled', 'code': 'session_cancelled'})
        self.assertNotIn('secret-cancel-value', json.dumps([activity.to_stream_dict() for activity in activities]))
        release.assert_called_once()
        self.assertEqual(release.call_args.args[0], str(child.id))

    @expectLogItems(
        [
            ExpectLogItem(
                'apps.runner.tasks',
                logging.ERROR,
                r'Interrupted session',
                count=1,
            )
        ]
    )
    def test_base_level_exit_does_not_finalize_success_and_propagates(self) -> None:
        """A non-cancellation base-level exit remains failed and preserves its type."""
        session = make_test_session('task-base-exit')
        fault = KeyboardInterrupt('header=secret-interrupt-value')
        runner = MagicMock()
        runner.run.side_effect = fault

        with (
            patch('apps.runner.tasks.try_acquire_lock', return_value=True),
            patch('apps.runner.tasks.release_lock'),
            patch('apps.runner.tasks.SessionRunner.for_session', return_value=runner),
            self.assertRaises(KeyboardInterrupt) as caught,
        ):
            run_session.run(str(session.id))

        session.refresh_from_db()
        failure = session.activities.get(kind=AgentSessionActivityKind.FAILURE)
        self.assertIs(caught.exception, fault)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)
        self.assertEqual(
            failure.details,
            {'message': 'Session worker interrupted', 'code': 'session_interrupted'},
        )
        self.assertNotIn('secret-interrupt-value', json.dumps(failure.to_stream_dict()))

    @patch('apps.runner.backends.django.mailbox_drain', return_value=[])
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.runner.tasks.release_lock')
    @patch('apps.runner.tasks.try_acquire_lock', return_value=True)
    @patch('apps.runner.loop.make_provider')
    def test_resume_emits_restart_and_reuses_activity_history(
        self,
        mock_provider_cls: Any,
        _lock: Any,
        _unlock: Any,
        _mailbox: Any,
    ) -> None:
        session = make_test_session('resume-agent')
        AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            revision=1,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
            details={'content': 'hello'},
        )
        AgentSessionActivity.objects.create(
            session=session,
            seq=2,
            revision=1,
            kind=AgentSessionActivityKind.OUTPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='output',
            details={'content': 'prior turn'},
        )
        session.status = AgentSessionStatus.WAITING
        session.save(update_fields=['status'])

        result = StreamResult(content='continued', latency_ms=1)
        mock_provider_cls.return_value.collect.return_value = result

        run_session.run(str(session.id))

        session.refresh_from_db()
        kinds = list(AgentSessionActivity.objects.filter(session=session).values_list('kind', flat=True))
        self.assertIn(AgentSessionActivityKind.RESTART, kinds)
        self.assertEqual(kinds.count(AgentSessionActivityKind.OUTPUT), 2)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)

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
    ) -> None:
        session = make_test_session('fresh-agent')
        run_session.run(str(session.id))

        session.refresh_from_db()
        kinds = list(AgentSessionActivity.objects.filter(session=session).values_list('kind', flat=True))
        self.assertNotIn(AgentSessionActivityKind.RESTART, kinds)
        self.assertNotIn(AgentSessionActivityKind.OUTPUT, kinds)
        self.assertEqual(session.status, AgentSessionStatus.WAITING)
        mock_provider_cls.assert_not_called()

    @patch('apps.runner.backends.django.mailbox_drain', return_value=[])
    @patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'})
    @patch('apps.runner.tasks.release_lock')
    @patch('apps.runner.tasks.try_acquire_lock', return_value=True)
    @patch('apps.runner.loop.make_provider')
    def test_schedule_trigger_session_terminates_after_turn(
        self,
        mock_provider_cls: Any,
        _lock: Any,
        _unlock: Any,
        _mailbox: Any,
    ) -> None:
        session = make_test_session('schedule-end-agent')
        agent = session.agent
        config = persist_agent_config(
            agent,
            AgentConfigSpec(
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hello',
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(
                        name='sweep',
                        kind='schedule',
                        cron='0 * * * *',
                        prompt='Run scheduled tasks.',
                    ),
                ],
            ),
            source_rev='schedule-end-v1',
        )
        schedule_trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        session.delete()
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            trigger_ref=schedule_trigger.id,
            trigger_type=TriggerType.TRIGGER,
            status=AgentSessionStatus.QUEUED,
        )
        with patch('apps.sessions.services.commands._schedule_generate_session_name'):
            DjangoSessionBackend(session).record_input('Run scheduled tasks.')

        result = StreamResult(content='done', latency_ms=1)
        mock_provider_cls.return_value.collect.return_value = result

        run_session.run(str(session.id))

        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.DONE)
        self.assertIsNotNone(session.ended_at)
