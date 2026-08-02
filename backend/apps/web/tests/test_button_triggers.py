# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for button trigger rendering and run endpoint."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from apps.agents.ingest import persist_agent_config
from apps.agents.models import AgentStatus, Trigger
from apps.agents.services.config_commands import create_from_example
from apps.runner.start import start_manual_session
from apps.sessions.models import AgentSession
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

BUTTON_TEXT = 'Run triage'
BUTTON_PROMPT = 'Run triage now.'


def _button_agent_config(*, triggers: list[TriggerSpec] | None = None) -> AgentConfigSpec:
    """Minimal agent config with manual and button triggers for web UI tests."""
    return AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='hello',
        triggers=triggers
        or [
            TriggerSpec(name='manual', kind='manual'),
            TriggerSpec(name='triage', kind='button', button_text=BUTTON_TEXT, prompt=BUTTON_PROMPT),
        ],
    )


class TestButtonTriggersWeb(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='button-triggers-user', password='test')
        self.other = User.objects.create_user(username='other-button-triggers-user', password='test')
        self.agent = create_from_example(
            self.user,
            'clock-assistant',
            identifier='button-triggers-agent',
        )
        persist_agent_config(
            self.agent,
            _button_agent_config(),
            source_rev='button-triggers-v1',
        )
        self.trigger = Trigger.objects.get(agent=self.agent, name='triage')

    def test_agent_detail_renders_button_text(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, BUTTON_TEXT)
        self.assertContains(
            response,
            reverse(
                'agent_run_button_trigger',
                kwargs={'agent_id': self.agent.id, 'trigger_id': self.trigger.id},
            ),
        )

    def test_session_detail_renders_button_text(self) -> None:
        self.client.force_login(self.user)
        session = start_manual_session(self.agent)
        response = self.client.get(reverse('session_detail', kwargs={'session_id': session.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, BUTTON_TEXT)
        self.assertContains(
            response,
            reverse(
                'agent_run_button_trigger',
                kwargs={'agent_id': self.agent.id, 'trigger_id': self.trigger.id},
            ),
        )

    @patch('apps.runner.dispatch.push_chat_and_dispatch')
    def test_run_button_creates_session_and_redirects(self, mock_push: MagicMock) -> None:
        self.client.force_login(self.user)
        before = AgentSession.objects.filter(agent=self.agent).count()
        response = self.client.post(
            reverse(
                'agent_run_button_trigger',
                kwargs={'agent_id': self.agent.id, 'trigger_id': self.trigger.id},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AgentSession.objects.filter(agent=self.agent).count(), before + 1)
        session = AgentSession.objects.filter(agent=self.agent).order_by('-created_at').first()
        assert session is not None
        self.assertEqual(
            response['Location'],
            reverse('session_detail', kwargs={'session_id': session.id}),
        )
        self.assertEqual(session.trigger_ref, self.trigger.id)
        mock_push.assert_called_once_with(session.id, BUTTON_PROMPT)

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Not Found: /agents/[0-9a-f-]+/triggers/[0-9a-f-]+/run/',
                count=1,
            )
        ]
    )
    def test_run_button_other_users_agent_not_found(self) -> None:
        self.client.force_login(self.other)
        response = self.client.post(
            reverse(
                'agent_run_button_trigger',
                kwargs={'agent_id': self.agent.id, 'trigger_id': self.trigger.id},
            ),
        )
        self.assertEqual(response.status_code, 404)

    def test_inactive_agent_hides_buttons(self) -> None:
        self.agent.status = AgentStatus.DISABLED
        self.agent.save(update_fields=['status'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': self.agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, BUTTON_TEXT)
