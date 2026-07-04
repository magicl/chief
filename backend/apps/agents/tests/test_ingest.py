# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent config ingest."""

from apps.agents.hardcoded import HARDCODED_SPEC, bootstrap_agent
from apps.agents.ingest import IngestError, create_agent_from_spec, validate_spec_tools
from apps.agents.models import Agent, AgentConfig, Trigger
from apps.agents.spec import AgentConfigSpec, LLMSpec, ToolInstance, TriggerSpec
from django.contrib.auth import get_user_model

from olib.py.django.test.cases import OTestCase


class ValidateSpecToolsTests(OTestCase):
    def test_valid_hardcoded_spec(self) -> None:
        validate_spec_tools(HARDCODED_SPEC)

    def test_unknown_tool_raises(self) -> None:
        spec = HARDCODED_SPEC.model_copy(
            update={'tools': [ToolInstance(id='missing', type='missing', allow=['*'])]},
        )
        with self.assertRaises(IngestError) as ctx:
            validate_spec_tools(spec)
        self.assertIn('missing', str(ctx.exception))

    def test_unknown_allow_function_raises(self) -> None:
        spec = HARDCODED_SPEC.model_copy(
            update={'tools': [ToolInstance(id='clock', type='clock', allow=['nope'])]},
        )
        with self.assertRaises(IngestError) as ctx:
            validate_spec_tools(spec)
        self.assertIn('nope', str(ctx.exception))

    def test_credential_ref_on_clock_rejected(self) -> None:
        spec = HARDCODED_SPEC.model_copy(
            update={'tools': [ToolInstance(id='clock', type='clock', credential_ref='x', allow=['now'])]},
        )
        with self.assertRaises(IngestError):
            validate_spec_tools(spec)


class CreateAgentFromSpecTests(OTestCase):
    def test_creates_agent_config_and_triggers(self) -> None:
        user = get_user_model().objects.create_user(username='ingest', password='x')
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            triggers=[TriggerSpec(name='manual', kind='manual')],
            tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
        )
        agent = create_agent_from_spec(user, spec, identifier='test-agent')

        self.assertEqual(agent.identifier, 'test-agent')
        self.assertIsNotNone(agent.current_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
        self.assertEqual(Trigger.objects.filter(agent=agent).count(), 1)

    def test_create_writes_spec_version_one(self) -> None:
        user = get_user_model().objects.create_user(username='sv', password='x')
        spec = HARDCODED_SPEC.model_copy()
        agent = create_agent_from_spec(user, spec, identifier='sv-agent')
        config = agent.current_config
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.spec_version, 1)
        self.assertEqual(config.spec['schema_version'], 1)

    def test_bootstrap_agent_delegates_to_ingest(self) -> None:
        user = get_user_model().objects.create_user(username='boot', password='x')
        agent = bootstrap_agent(user, provider='openai', model='gpt-5.4-mini', identifier='demo')
        self.assertTrue(Agent.objects.filter(pk=agent.pk).exists())
