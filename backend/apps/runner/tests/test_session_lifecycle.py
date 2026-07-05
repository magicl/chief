# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for automated trigger session finalization."""

from __future__ import annotations

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, Trigger
from apps.runner.session_lifecycle import finalize_automated_trigger_session
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model
from libs.agent_spec import AgentConfigSpec, LLMSpec, QueueSpec, TriggerSpec

from olib.py.django.test.cases import OTestCase


class TestFinalizeAutomatedTriggerSession(OTestCase):
    def _schedule_trigger(self) -> tuple[Agent, Trigger]:
        user = get_user_model().objects.create_user(username='finalize-sched', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Sched', identifier='finalize-sched-agent')
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
            source_rev='finalize-sched-v1',
        )
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='sweep')
        return agent, trigger

    def test_schedule_session_waiting_becomes_done(self) -> None:
        agent, trigger = self._schedule_trigger()
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id,
        )

        finalize_automated_trigger_session(session)

        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.DONE)
        self.assertIsNotNone(session.ended_at)

    def test_manual_session_waiting_is_unchanged(self) -> None:
        agent, _schedule = self._schedule_trigger()
        manual = Trigger.objects.get(agent=agent, name='manual')
        config = agent.current_config
        assert config is not None
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=manual.id,
        )

        finalize_automated_trigger_session(session)

        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.WAITING)
        self.assertIsNone(session.ended_at)

    def test_queue_session_waiting_becomes_done(self) -> None:
        user = get_user_model().objects.create_user(username='finalize-queue', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Queue', identifier='finalize-queue-agent')
        config = persist_agent_config(
            agent,
            AgentConfigSpec(
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hello',
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(
                        name='worker',
                        kind='queue',
                        queue='inbox',
                        prompt='Process queue items.',
                    ),
                ],
                queues=[QueueSpec(id='inbox')],
            ),
            source_rev='finalize-queue-v1',
        )
        trigger = Trigger.objects.get(agent=agent, agent_config=config, name='worker')
        session = AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.WAITING,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id,
        )

        finalize_automated_trigger_session(session)

        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.DONE)
        self.assertIsNotNone(session.ended_at)
