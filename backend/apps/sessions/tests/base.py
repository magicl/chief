# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.hardcoded import bootstrap_agent
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model


def make_test_session(identifier: str = 'test-agent') -> AgentSession:
    user = get_user_model().objects.create_user(username=f'user-{identifier}', password='test')
    agent = bootstrap_agent(
        user,
        identifier=identifier,
        provider='openai',
        model='gpt-5.4-mini',
    )
    config = agent.current_config
    assert config is not None
    trigger = agent.triggers.filter(name='manual').first()
    return AgentSession.objects.create(
        agent=agent,
        agent_config=config,
        status=AgentSessionStatus.QUEUED,
        trigger_type=TriggerType.TRIGGER,
        trigger_ref=trigger.id if trigger else None,
    )
