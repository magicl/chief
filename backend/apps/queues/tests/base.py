# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from apps.agents.hardcoded import bootstrap_agent
from apps.agents.models import Agent, AgentConfig
from apps.queues.models import Queue, Source
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model


def make_test_queue(
    *,
    identifier: str = 'queue-agent',
    queue_id: str = 'inbox',
    max_attempts: int = 3,
) -> tuple[Queue, AgentSession]:
    user = get_user_model().objects.create_user(username=f'user-{identifier}', password='test')
    agent = bootstrap_agent(
        user,
        identifier=identifier,
        provider='openai',
        model='gpt-5.4-mini',
    )
    config = agent.current_config
    assert config is not None
    queue = Queue.objects.create(
        agent=agent,
        queue_id=queue_id,
        agent_config=config,
        max_attempts=max_attempts,
    )
    trigger = agent.triggers.filter(name='manual').first()
    session = AgentSession.objects.create(
        agent=agent,
        agent_config=config,
        status=AgentSessionStatus.RUNNING,
        trigger_type=TriggerType.TRIGGER,
        trigger_ref=trigger.id if trigger else None,
    )
    return queue, session


def make_test_source(queue: Queue, *, source_id: str = 'test-src') -> Source:
    return Source.objects.create(
        queue=queue,
        source_id=source_id,
        adapter_type='test',
        config={'prefix': 'x'},
    )


def make_second_session(agent: Agent, config: AgentConfig) -> AgentSession:
    trigger = agent.triggers.filter(name='manual').first()
    return AgentSession.objects.create(
        agent=agent,
        agent_config=config,
        status=AgentSessionStatus.RUNNING,
        trigger_type=TriggerType.TRIGGER,
        trigger_ref=trigger.id if trigger else None,
    )
