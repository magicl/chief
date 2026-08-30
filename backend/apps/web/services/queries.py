# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only queries for web views (dashboard, agent detail, session detail)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from apps.agents.block_gate import BlockGateResult, blocks_allow_dispatch
from apps.agents.models import Agent, AgentStatus, Trigger, TriggerKind, TriggerStatus
from apps.agents.services.config_sync import config_source_label
from apps.sessions.models import AgentSession
from apps.sessions.services.queries import activities_for
from django.db.models import QuerySet
from django.http import Http404
from libs.agent_spec import list_examples
from libs.agent_spec.example_catalog import ExampleSpecInfo

RECENT_SESSIONS_LIMIT = 20


@dataclass(frozen=True)
class DirectParentInfo:
    """Direct owned parent session reference for web composition.

    ``name`` is the stored session name, or null when unset. Callers must not
    invent a hex display fallback here — templates/UI may apply one.
    """

    id: UUID
    name: str | None


def get_owned_direct_parent(session: AgentSession, *, user_id: int) -> DirectParentInfo | None:
    """Return the direct parent when it exists and is owned by ``user_id``.

    Uses a single lookup on ``parent_session_id``. Missing or foreign-owned
    parents yield None (same presentation as a root session). Does not walk
    the full ancestor breadcrumb chain.
    """
    parent_id = session.parent_session_id
    if parent_id is None:
        return None
    parent = AgentSession.objects.filter(pk=parent_id, agent__user_id=user_id).only('id', 'name').first()
    if parent is None:
        return None
    return DirectParentInfo(id=parent.id, name=parent.name)


@dataclass(frozen=True)
class DashboardData:
    """Everything the dashboard template needs."""

    agents: QuerySet[Agent]
    sessions: QuerySet[AgentSession]
    examples: list[ExampleSpecInfo]


def get_dashboard_data(*, user_id: int | None) -> DashboardData:
    """Fetch dashboard listing, scoped to the authenticated user.

    Anonymous users see an empty dashboard (no agent or session data is exposed).
    """
    agents = Agent.objects.select_related('current_config', 'user').order_by('-id')
    sessions = AgentSession.objects.select_related('agent').order_by('-created_at')

    if user_id is not None:
        agents = agents.filter(user_id=user_id)
        sessions = sessions.filter(agent__user_id=user_id)
        examples = list_examples()
    else:
        agents = agents.none()
        sessions = AgentSession.objects.none()
        examples = []

    return DashboardData(
        agents=agents,
        sessions=sessions[:RECENT_SESSIONS_LIMIT],
        examples=examples,
    )


@dataclass(frozen=True)
class ButtonTriggerInfo:
    """Render-safe button trigger state including its current block verdict."""

    id: UUID
    button_text: str
    blocked: bool
    block_reason: str


def list_active_button_triggers(agent: Agent) -> list[ButtonTriggerInfo]:
    """Return current button triggers with render-time readiness in YAML order."""
    if agent.status != AgentStatus.ACTIVE or agent.current_config is None:
        return []
    triggers = list(
        Trigger.objects.filter(
            agent=agent,
            agent_config=agent.current_config,
            kind=TriggerKind.BUTTON,
            status=TriggerStatus.ACTIVE,
        ).order_by('id')
    )
    results: list[ButtonTriggerInfo] = []
    for trigger in triggers:
        gate = blocks_allow_dispatch(agent, trigger)
        results.append(
            ButtonTriggerInfo(
                id=trigger.id,
                button_text=str(trigger.spec['button_text']),
                blocked=not gate.ready,
                block_reason=gate.reason,
            )
        )
    return results


def get_manual_trigger_gate(agent: Agent) -> BlockGateResult:
    """Return the active manual trigger's block verdict for new-session controls."""
    if agent.status != AgentStatus.ACTIVE or agent.current_config is None:
        return BlockGateResult(ready=True)
    trigger = Trigger.objects.filter(
        agent=agent,
        agent_config=agent.current_config,
        kind=TriggerKind.MANUAL,
        status=TriggerStatus.ACTIVE,
    ).first()
    if trigger is None:
        return BlockGateResult(ready=True)
    return blocks_allow_dispatch(agent, trigger)


def get_active_button_trigger(agent: Agent, trigger_id: UUID) -> Trigger:
    """Return one active button trigger on the agent's current config, or raise Http404."""
    if agent.status != AgentStatus.ACTIVE or agent.current_config is None:
        raise Http404('Trigger not found')
    try:
        return Trigger.objects.get(
            id=trigger_id,
            agent=agent,
            agent_config=agent.current_config,
            kind=TriggerKind.BUTTON,
            status=TriggerStatus.ACTIVE,
        )
    except Trigger.DoesNotExist as exc:
        raise Http404('Trigger not found') from exc


def get_owned_agent(user_id: int, agent_id: UUID) -> Agent:
    """Return an agent owned by user_id, or raise Http404."""
    try:
        return Agent.objects.get(pk=agent_id, user_id=user_id)
    except Agent.DoesNotExist as exc:
        raise Http404('Agent not found') from exc


def get_owned_session(user_id: int, session_id: UUID) -> AgentSession:
    """Return a session whose agent is owned by user_id, or raise Http404."""
    try:
        return AgentSession.objects.select_related('agent', 'agent_config').get(
            pk=session_id,
            agent__user_id=user_id,
        )
    except AgentSession.DoesNotExist as exc:
        raise Http404('Session not found') from exc


@dataclass(frozen=True)
class AgentDetailData:
    """Everything the agent detail template needs."""

    agent: Agent
    sessions: QuerySet[AgentSession]
    source_label: str
    config_dirty: bool


def get_agent_detail_data(user_id: int, agent_id: UUID) -> AgentDetailData:
    """Fetch agent detail page data, enforcing ownership."""
    agent = get_owned_agent(user_id, agent_id)
    sessions = AgentSession.objects.filter(agent=agent).order_by('-created_at')
    return AgentDetailData(
        agent=agent,
        sessions=sessions,
        source_label=config_source_label(agent.config_source),
        config_dirty=agent.current_config.dirty if agent.current_config else False,
    )


def get_session_llm_label(session: AgentSession) -> str:
    """Human-readable LLM provider/model label for a session."""
    spec = session.agent_config.spec if session.agent_config else {}
    llm = spec.get('llm', {})
    provider = llm.get('provider', '')
    model = llm.get('model', '')
    if provider and model:
        return f'{provider} / {model}'
    return model or '—'


def get_credential_for_write_check(user_id: int, name: str) -> Any | None:
    """Look up a credential row to check write eligibility (disk vs UI source)."""
    from apps.keys.models import UserCredential

    return UserCredential.objects.filter(user_id=user_id, name=name).first()


def get_activity_snapshot(user_id: int, session_id: UUID) -> dict[str, Any]:
    """Return owned session metadata plus that session's activities as stream dicts.

    Activities come only from the requested session (never a child session's rows).
    ``session.parent`` is the direct parent ``{id, name}`` when one exists, else null.
    Parent ``name`` is the stored name or null (no hex display fallback).
    """
    session = get_owned_session(user_id, session_id)
    direct_parent = get_owned_direct_parent(session, user_id=user_id)
    parent_payload: dict[str, Any] | None = None
    if direct_parent is not None:
        parent_payload = {
            'id': str(direct_parent.id),
            'name': direct_parent.name,
        }
    return {
        'session': {
            'id': str(session.id),
            'name': session.name,
            'status': session.status,
            'parent_session_id': str(session.parent_session_id) if session.parent_session_id else None,
            'parent': parent_payload,
        },
        'activities': [activity.to_stream_dict() for activity in activities_for(session)],
    }
