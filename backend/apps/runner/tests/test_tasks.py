# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any
from unittest.mock import patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Trigger
from apps.runner.tasks import run_session
from apps.sessions.events import append_event, events_for
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus
from apps.sessions.tests.base import make_test_session
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec
from libs.providers.llm.base import StreamResult

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

    @patch('apps.runner.backends.django.publish_session_event')
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
        _publish: Any,
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
        session.agent_config = config
        session.trigger_ref = schedule_trigger.id
        session.status = AgentSessionStatus.QUEUED
        session.save(update_fields=['agent_config', 'trigger_ref', 'status'])
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'Run scheduled tasks.'})

        result = StreamResult(content='done', latency_ms=1)
        mock_provider_cls.return_value.collect.return_value = result

        run_session.run(str(session.id))

        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.DONE)
        self.assertIsNotNone(session.ended_at)
