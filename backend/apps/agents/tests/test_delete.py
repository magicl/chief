# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.delete import AgentNotFoundError, delete_agent_for_user
from apps.agents.services.config_commands import create_from_example
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.contrib.auth import get_user_model

from olib.py.django.test.cases import OTestCase


class TestDeleteAgent(OTestCase):
    def test_delete_agent_for_user_removes_agent(self) -> None:
        user = get_user_model().objects.create_user(username='delete-user', password='test')
        agent = create_from_example(user, 'clock-assistant')
        delete_agent_for_user(user, agent.id)
        self.assertFalse(agent.__class__.objects.filter(pk=agent.id).exists())

    def test_delete_agent_for_user_cascades_sessions(self) -> None:
        user = get_user_model().objects.create_user(username='delete-sessions-user', password='test')
        agent = create_from_example(user, 'clock-assistant')
        config = agent.current_config
        assert config is not None
        trigger = agent.triggers.filter(name='manual').first()
        AgentSession.objects.create(
            agent=agent,
            agent_config=config,
            status=AgentSessionStatus.QUEUED,
            trigger_type=TriggerType.TRIGGER,
            trigger_ref=trigger.id if trigger else None,
        )
        self.assertTrue(AgentSession.objects.filter(agent=agent).exists())
        delete_agent_for_user(user, agent.id)
        self.assertFalse(AgentSession.objects.filter(agent_id=agent.id).exists())

    def test_delete_agent_for_user_rejects_other_owner(self) -> None:
        User = get_user_model()
        owner = User.objects.create_user(username='owner', password='test')
        other = User.objects.create_user(username='other', password='test')
        agent = create_from_example(owner, 'clock-assistant')
        with self.assertRaises(AgentNotFoundError):
            delete_agent_for_user(other, agent.id)
