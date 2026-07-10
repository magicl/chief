# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Start a new agent session from the manual trigger."""

from __future__ import annotations

from apps.agents.models import Agent, AgentStatus, Trigger, TriggerKind, TriggerStatus
from apps.runner.session_start import StartSessionError, start_trigger_session
from apps.sessions.models import AgentSession, AgentSessionStatus
from django.utils import timezone


def start_manual_session(agent: Agent, *, initial_message: str = '') -> AgentSession:
    """Create and queue a session from an active agent's manual trigger."""
    if agent.status != AgentStatus.ACTIVE:
        raise StartSessionError(f'Agent {agent.identifier!r} is disabled')

    if agent.current_config is None:
        raise StartSessionError(f'Agent {agent.identifier!r} has no current config')

    trigger = Trigger.objects.filter(
        agent=agent,
        agent_config=agent.current_config,
        kind=TriggerKind.MANUAL,
        status=TriggerStatus.ACTIVE,
    ).first()
    if trigger is None:
        raise StartSessionError(f'No active manual trigger for agent {agent.identifier!r}')

    session = start_trigger_session(agent, trigger)

    initial = initial_message.strip()
    if initial:
        from apps.runner.dispatch import push_chat_and_dispatch

        push_chat_and_dispatch(session.id, initial)
    else:
        session.status = AgentSessionStatus.WAITING
        session.started_at = timezone.now()
        session.save(update_fields=['status', 'started_at'])

    return session
