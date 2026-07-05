# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Create agent sessions from triggers without importing dispatch."""

from __future__ import annotations

from apps.agents.models import Agent, Trigger, TriggerStatus
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType


class StartSessionError(Exception):
    """Agent is not ready to start a session from the requested trigger."""


def start_trigger_session(agent: Agent, trigger: Trigger) -> AgentSession:
    """Create a queued session bound to an active trigger on the agent's current config."""
    if agent.current_config is None:
        raise StartSessionError(f'Agent {agent.identifier!r} has no current config')

    if trigger.agent_id != agent.pk:
        raise StartSessionError(f'Trigger {trigger.name!r} does not belong to agent {agent.identifier!r}')

    if trigger.agent_config_id != agent.current_config_id:
        raise StartSessionError(
            f'Trigger {trigger.name!r} is not on agent {agent.identifier!r} current config'
        )

    if trigger.status != TriggerStatus.ACTIVE:
        raise StartSessionError(f'Trigger {trigger.name!r} is not active')

    return AgentSession.objects.create(
        agent=agent,
        agent_config=agent.current_config,
        status=AgentSessionStatus.QUEUED,
        trigger_type=TriggerType.TRIGGER,
        trigger_ref=trigger.id,
    )
