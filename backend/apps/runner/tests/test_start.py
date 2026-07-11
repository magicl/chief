# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for manual agent session starts."""

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent, AgentStatus
from apps.runner.session_start import StartSessionError
from apps.runner.start import start_manual_session
from django.contrib.auth import get_user_model
from libs.agent_spec import AgentConfigSpec, LLMSpec, TriggerSpec

from olib.py.django.test.cases import OTestCase


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
