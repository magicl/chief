# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Start a new agent session from the manual trigger."""

from __future__ import annotations

from apps.agents.models import Agent, AgentStatus, Trigger, TriggerKind, TriggerStatus
from apps.runner.session_start import StartSessionError, start_trigger_session
from apps.sessions.models import AgentSession, AgentSessionStatus
from apps.sessions.services.commands import set_session_status
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
        set_session_status(session, AgentSessionStatus.WAITING, started_at=timezone.now())

    return session


def start_button_session(agent: Agent, trigger: Trigger) -> AgentSession:
    """Start a new session from an active button trigger and dispatch its prompt.

    Capacity is re-checked under a trigger row lock so concurrent clicks cannot
    exceed ``max_sessions``. Prompt dispatch runs after the lock is released.
    """
    from apps.runner.budget_gate import budget_allows_dispatch
    from apps.runner.dispatch import push_chat_and_dispatch
    from apps.runner.scheduling import trigger_has_capacity, trigger_prompt
    from django.db import transaction

    if agent.status != AgentStatus.ACTIVE:
        raise StartSessionError(f'Agent {agent.identifier!r} is disabled')
    if trigger.kind != TriggerKind.BUTTON:
        raise StartSessionError(f'Trigger {trigger.name!r} is not a button trigger')
    # Budget check outside the atomic block to avoid holding the row lock during spend queries.
    if not budget_allows_dispatch(agent):
        raise StartSessionError(f'Agent {agent.identifier!r} is over budget')

    with transaction.atomic():
        try:
            locked = Trigger.objects.select_for_update().select_related('agent').get(pk=trigger.pk)
        except Trigger.DoesNotExist as exc:
            raise StartSessionError('button trigger does not exist') from exc
        if locked.agent.status != AgentStatus.ACTIVE:
            raise StartSessionError(f'Agent {locked.agent.identifier!r} is disabled')
        if locked.kind != TriggerKind.BUTTON:
            raise StartSessionError(f'Trigger {locked.name!r} is not a button trigger')
        if not trigger_has_capacity(locked):
            raise StartSessionError(f'Trigger {locked.name!r} is at max_sessions capacity')
        session = start_trigger_session(locked.agent, locked)
        Trigger.objects.filter(pk=locked.pk).update(last_fired_at=timezone.now())

    push_chat_and_dispatch(session.id, trigger_prompt(locked))
    return session
