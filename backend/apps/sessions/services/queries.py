# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only session domain queries."""

from __future__ import annotations

from uuid import UUID

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
)


def get_session_name(session_id: UUID) -> str | None:
    """Return a session's optional display name."""
    return AgentSession.objects.filter(pk=session_id).values_list('name', flat=True).first()


def get_algorithm_session_for_target(
    user_id: int,
    algorithm_id: str,
    target_session_id: UUID,
) -> AgentSession | None:
    """Return this user's algorithm session that already targets one session.

    Algorithm sessions have no target column: the session they act on is
    recorded as ``target_session_id`` on their root activity, so a retried
    one-off job can find and reuse the session it created earlier instead of
    opening a second one. Returns the oldest match when a retry raced.
    """
    return (
        AgentSession.objects.filter(
            user_id=user_id,
            algorithm_id=algorithm_id,
            activities__parent_id=None,
            activities__details__target_session_id=str(target_session_id),
        )
        .order_by('created_at')
        .first()
    )


def activities_for(session: AgentSession | UUID) -> list[AgentSessionActivity]:
    """Return session activities in immutable creation order."""
    session_id = session if isinstance(session, UUID) else session.id
    return list(AgentSessionActivity.objects.filter(session_id=session_id).order_by('seq'))


def activity_belongs_to_session(
    session: AgentSession | UUID,
    activity_id: UUID,
) -> bool:
    """Return whether an activity is owned by the specified session."""
    session_id = session if isinstance(session, UUID) else session.id
    return AgentSessionActivity.objects.filter(
        pk=activity_id,
        session_id=session_id,
    ).exists()


def subagent_activity_for_child(child_session_id: UUID) -> AgentSessionActivity:
    """Return the unique parent reference for a linked child session."""
    return AgentSessionActivity.objects.get(
        child_session_id=child_session_id,
        kind=AgentSessionActivityKind.SUBAGENT,
    )


def get_first_input_text(session_id: UUID) -> str | None:
    """Return trimmed content from the first input activity, when textual."""
    details = (
        AgentSessionActivity.objects.filter(
            session_id=session_id,
            kind=AgentSessionActivityKind.INPUT,
        )
        .order_by('seq')
        .values_list('details', flat=True)
        .first()
    )
    if not details:
        return None
    content = details.get('content', '')
    if not isinstance(content, str):
        return None
    text = content.strip()
    return text or None


def input_activity_count(session_id: UUID) -> int:
    """Count canonical user-input activities for a session."""
    return AgentSessionActivity.objects.filter(
        session_id=session_id,
        kind=AgentSessionActivityKind.INPUT,
    ).count()


def child_sessions_for(
    session: AgentSession | UUID,
    *,
    user_id: int,
) -> list[AgentSession]:
    """Return direct children only when the caller owns the parent session."""
    session_id = session if isinstance(session, UUID) else session.id
    if not AgentSession.objects.filter(
        pk=session_id,
        agent__user_id=user_id,
    ).exists():
        return []
    return list(
        AgentSession.objects.filter(
            parent_session_id=session_id,
            agent__user_id=user_id,
        ).order_by('created_at')
    )


def parent_session_breadcrumb(
    session: AgentSession,
    *,
    user_id: int,
) -> list[AgentSession]:
    """Walk owned ancestors nearest-first without crossing an owner boundary."""
    if not AgentSession.objects.filter(
        pk=session.id,
        agent__user_id=user_id,
    ).exists():
        return []
    chain: list[AgentSession] = []
    # Including the starting session prevents corrupt cycles from adding it as
    # its own ancestor.
    seen = {session.id}
    current_id = session.parent_session_id
    while current_id is not None and current_id not in seen:
        current = (
            AgentSession.objects.select_related('agent')
            .only('id', 'parent_session_id', 'agent__user_id')
            .get(pk=current_id)
        )
        if current.agent.user_id != user_id:
            break
        seen.add(current.id)
        chain.append(current)
        current_id = current.parent_session_id
    return chain
