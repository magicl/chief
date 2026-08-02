# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for manual and button agent session starts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, AgentStatus, Trigger, TriggerKind
from apps.runner.session_lifecycle import (
    _AUTOMATED_TERMINATE_KINDS,
    finalize_automated_trigger_session,
)
from apps.runner.session_start import StartSessionError
from apps.runner.start import start_button_session, start_manual_session
from apps.sessions.models import AgentSessionStatus
from django.contrib.auth import get_user_model
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec

from olib.py.django.test.cases import OTestCase

BUTTON_PROMPT = 'Run triage now.'


def _button_agent_config(*, triggers: list[TriggerSpec] | None = None) -> AgentConfigSpec:
    """Minimal agent config with a button trigger for session-start tests."""
    return AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        triggers=triggers
        or [
            TriggerSpec(name='manual', kind='manual'),
            TriggerSpec(name='triage', kind='button', button_text='Triage', prompt=BUTTON_PROMPT),
        ],
    )


class TestStartManualSession(OTestCase):
    def test_disabled_agent_is_rejected(self) -> None:
        user = get_user_model().objects.create_user(username='manual-disabled', password='x')
        agent = Agent.objects.create(
            user_id=user.pk,
            name='Disabled',
            identifier='manual-disabled-agent',
            status=AgentStatus.DISABLED,
        )
        persist_agent_config(
            agent,
            AgentConfigSpec(
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hello',
                triggers=[TriggerSpec(name='manual', kind='manual')],
            ),
            source_rev='manual-disabled-v1',
        )

        with self.assertRaisesRegex(StartSessionError, 'disabled'):
            start_manual_session(agent)


class TestStartButtonSession(OTestCase):
    def _button_agent(self) -> tuple[Agent, Trigger]:
        """Create an active agent with manual and button triggers on current config."""
        user = get_user_model().objects.create_user(username='button-start', password='x')
        agent = Agent.objects.create(
            user_id=user.pk,
            name='Button',
            identifier='button-start-agent',
            status=AgentStatus.ACTIVE,
        )
        persist_agent_config(
            agent,
            _button_agent_config(),
            source_rev='button-start-v1',
        )
        trigger = Trigger.objects.get(agent=agent, name='triage')
        return agent, trigger

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_start_button_session_dispatches_prompt(self, mock_push: MagicMock) -> None:
        agent, trigger = self._button_agent()

        session = start_button_session(agent, trigger)

        self.assertEqual(session.trigger_ref, trigger.id)
        mock_push.assert_called_once_with(session.id, BUTTON_PROMPT)
        trigger.refresh_from_db()
        self.assertIsNotNone(trigger.last_fired_at)

    def test_start_button_rejects_wrong_kind(self) -> None:
        agent, _button = self._button_agent()
        manual = Trigger.objects.get(agent=agent, name='manual')

        with self.assertRaisesRegex(StartSessionError, 'not a button trigger'):
            start_button_session(agent, manual)

    def test_start_button_rejects_disabled_agent(self) -> None:
        agent, trigger = self._button_agent()
        agent.status = AgentStatus.DISABLED
        agent.save(update_fields=['status'])

        with self.assertRaisesRegex(StartSessionError, 'disabled'):
            start_button_session(agent, trigger)

    @patch('apps.runner.budget_gate.budget_allows_dispatch', return_value=False)
    def test_start_button_rejects_when_budget_blocked(self, _mock_budget: MagicMock) -> None:
        agent, trigger = self._button_agent()

        with self.assertRaisesRegex(StartSessionError, 'over budget'):
            start_button_session(agent, trigger)

    @patch('apps.runner.scheduling.trigger_has_capacity', return_value=False)
    def test_start_button_rejects_when_at_capacity(self, _mock_capacity: MagicMock) -> None:
        agent, trigger = self._button_agent()

        with self.assertRaisesRegex(StartSessionError, 'max_sessions capacity'):
            start_button_session(agent, trigger)

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_start_button_respects_real_max_sessions(self, mock_push: MagicMock) -> None:
        """A second start fails when an in-flight session already fills max_sessions=1."""
        user = get_user_model().objects.create_user(username='button-cap', password='x')
        agent = Agent.objects.create(
            user_id=user.pk,
            name='Button Cap',
            identifier='button-cap-agent',
            status=AgentStatus.ACTIVE,
        )
        persist_agent_config(
            agent,
            _button_agent_config(
                triggers=[
                    TriggerSpec(name='manual', kind='manual'),
                    TriggerSpec(
                        name='triage',
                        kind='button',
                        button_text='Triage',
                        prompt=BUTTON_PROMPT,
                        max_sessions=1,
                    ),
                ],
            ),
            source_rev='button-cap-v1',
        )
        trigger = Trigger.objects.get(agent=agent, name='triage')

        first = start_button_session(agent, trigger)
        self.assertEqual(first.trigger_ref, trigger.id)
        mock_push.assert_called_once()

        with self.assertRaisesRegex(StartSessionError, 'max_sessions capacity'):
            start_button_session(agent, trigger)

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_button_session_stays_waiting_after_finalize(self, _mock_push: MagicMock) -> None:
        """Button sessions remain chatable; finalize must not force DONE."""
        agent, trigger = self._button_agent()
        session = start_button_session(agent, trigger)
        session.status = AgentSessionStatus.WAITING
        session.save(update_fields=['status'])

        finalize_automated_trigger_session(session)
        session.refresh_from_db()
        self.assertEqual(session.status, AgentSessionStatus.WAITING)

    def test_button_kind_is_not_auto_terminated(self) -> None:
        self.assertNotIn(TriggerKind.BUTTON, _AUTOMATED_TERMINATE_KINDS)
