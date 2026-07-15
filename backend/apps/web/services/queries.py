# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only queries for web views (dashboard, agent detail, session detail)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from apps.agents.models import Agent
from apps.agents.services.config_sync import config_source_label
from apps.sessions.models import AgentSession
from django.db.models import QuerySet
from django.http import Http404
from libs.agent_spec import list_examples
from libs.agent_spec.example_catalog import ExampleSpecInfo

RECENT_SESSIONS_LIMIT = 20


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
