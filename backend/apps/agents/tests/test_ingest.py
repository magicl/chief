# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent config ingest."""

from apps.agents.ingest import (
    IngestError,
    create_agent_from_spec,
    persist_agent_config,
    validate_spec_tools,
)
from apps.agents.models import Agent, AgentConfig, Trigger
from apps.agents.services.config_commands import create_from_example
from apps.queues.models import Queue, Source
from django.contrib.auth import get_user_model
from libs.agent_spec import (
    AgentConfigSpec,
    LLMSpec,
    QueueSpec,
    SourceSpec,
    ToolInstance,
    TriggerSpec,
    load_example,
)

from olib.py.django.test.cases import OTestCase

CLOCK_SPEC = load_example('clock-assistant')


class ValidateSpecToolsTests(OTestCase):
    def test_valid_clock_example_spec(self) -> None:
        validate_spec_tools(CLOCK_SPEC)

    def test_unknown_tool_raises(self) -> None:
        spec = CLOCK_SPEC.model_copy(
            update={'tools': [ToolInstance(id='missing', type='missing', allow=['*'])]},
        )
        with self.assertRaises(IngestError) as ctx:
            validate_spec_tools(spec)
        self.assertIn('missing', str(ctx.exception))

    def test_unknown_allow_function_raises(self) -> None:
        spec = CLOCK_SPEC.model_copy(
            update={'tools': [ToolInstance(id='clock', type='clock', allow=['nope'])]},
        )
        with self.assertRaises(IngestError) as ctx:
            validate_spec_tools(spec)
        self.assertIn('nope', str(ctx.exception))

    def test_credential_ref_on_clock_rejected(self) -> None:
        spec = CLOCK_SPEC.model_copy(
            update={'tools': [ToolInstance(id='clock', type='clock', credential_ref='x', allow=['now'])]},
        )
        with self.assertRaises(IngestError):
            validate_spec_tools(spec)


class CreateAgentFromSpecTests(OTestCase):
    def test_create_defaults_to_active_with_blank_source_path(self) -> None:
        user = get_user_model().objects.create_user(username='ingest-defaults', password='x')
        agent = create_agent_from_spec(
            user,
            CLOCK_SPEC.model_copy(),
            name='Default agent',
            identifier='default-agent',
        )

        self.assertEqual(getattr(agent, 'status', None), 'active')
        self.assertEqual(getattr(agent, 'source_path', None), '')

    def test_create_sets_source_path(self) -> None:
        user = get_user_model().objects.create_user(username='ingest-disk', password='x')
        agent = create_agent_from_spec(
            user,
            CLOCK_SPEC.model_copy(),
            name='Disk agent',
            identifier='disk-agent',
            source_path='agents/disk-agent.yaml',
        )

        self.assertEqual(getattr(agent, 'source_path', None), 'agents/disk-agent.yaml')

    def test_creates_agent_config_and_triggers(self) -> None:
        user = get_user_model().objects.create_user(username='ingest', password='x')
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            triggers=[TriggerSpec(name='manual', kind='manual')],
            tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
        )
        agent = create_agent_from_spec(user, spec, name='Test agent', identifier='test-agent')

        self.assertEqual(agent.identifier, 'test-agent')
        self.assertIsNotNone(agent.current_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
        self.assertEqual(Trigger.objects.filter(agent=agent).count(), 1)

    def test_create_writes_current_spec_version(self) -> None:
        user = get_user_model().objects.create_user(username='sv', password='x')
        spec = CLOCK_SPEC.model_copy()
        agent = create_agent_from_spec(user, spec, name='SV agent', identifier='sv-agent')
        config = agent.current_config
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.spec_version, 2)
        self.assertEqual(config.spec['schema_version'], 2)

    def test_create_from_example_delegates_to_ingest(self) -> None:
        user = get_user_model().objects.create_user(username='boot', password='x')
        agent = create_from_example(user, 'clock-assistant', identifier='demo')
        self.assertTrue(Agent.objects.filter(pk=agent.pk).exists())

    def test_persist_spec_with_queues_materializes_queue_rows(self) -> None:
        user = get_user_model().objects.create_user(username='ingest-queue', password='x')

        agent = Agent.objects.create(user_id=user.pk, name='Queue ingest', identifier='queue-ingest-agent')
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            triggers=[TriggerSpec(name='manual', kind='manual')],
            tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
            queues=[
                QueueSpec(
                    id='inbox',
                    sources=[SourceSpec(id='poll-a', adapter_type='test', config={'prefix': 'in'})],
                ),
            ],
        )
        persist_agent_config(agent, spec, source_rev='with-queues')

        queue = Queue.objects.get(agent=agent, queue_id='inbox')
        self.assertTrue(Source.objects.filter(queue=queue, source_id='poll-a').exists())
