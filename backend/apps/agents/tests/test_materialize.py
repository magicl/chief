# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent config materialization."""

from __future__ import annotations

from apps.agents.hardcoded import HARDCODED_SPEC
from apps.agents.ingest import persist_agent_config
from apps.agents.materialize import materialize_agent_config
from apps.agents.models import Agent, AgentConfig, Trigger
from apps.queues.models import Queue, Source
from django.contrib.auth import get_user_model
from libs.agent_spec import (
    AgentConfigSpec,
    LLMSpec,
    QueueSpec,
    SourceSpec,
    ToolInstance,
    TriggerSpec,
)

from olib.py.django.test.cases import OTestCase


class TestMaterializeAgentConfig(OTestCase):
    def test_materialize_creates_triggers_and_queues(self) -> None:
        user = get_user_model().objects.create_user(username='mat-user', password='x')

        agent = Agent.objects.create(user_id=user.pk, identifier='mat-agent')
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            triggers=[TriggerSpec(name='manual', kind='manual')],
            tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
            queues=[
                QueueSpec(
                    id='inbox',
                    sources=[SourceSpec(id='test-src', adapter_type='test', config={'prefix': 'x'})],
                ),
            ],
        )
        config = persist_agent_config(agent, spec, source_rev='mat-v1')

        self.assertEqual(Trigger.objects.filter(agent=agent, agent_config=config).count(), 1)
        queue = Queue.objects.get(agent=agent, queue_id='inbox')
        self.assertEqual(queue.agent_config_id, config.id)
        self.assertTrue(Source.objects.filter(queue=queue, source_id='test-src').exists())

    def test_materialize_queues_only_when_present(self) -> None:
        user = get_user_model().objects.create_user(username='mat-no-queue', password='x')

        agent = Agent.objects.create(user_id=user.pk, identifier='mat-no-queue-agent')
        config = AgentConfig.objects.create(
            agent=agent,
            source_rev='bare',
            spec_version=1,
            spec=HARDCODED_SPEC.model_dump(mode='json'),
        )
        materialize_agent_config(agent, config, HARDCODED_SPEC)
        self.assertEqual(Queue.objects.filter(agent=agent).count(), 0)
