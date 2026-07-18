# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent lifecycle helpers."""

from __future__ import annotations

from uuid import UUID

from apps.agents.models import Agent
from apps.bus.resources import publish_resource_update_after_commit
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction


class AgentNotFoundError(LookupError):
    """Raised when the agent does not exist or is not owned by the user."""


@transaction.atomic
def delete_agent_for_user(user: AbstractBaseUser, agent_id: UUID) -> None:
    """Delete an owned agent and notify that owner after commit."""
    try:
        agent = Agent.objects.get(pk=agent_id, user_id=user.pk)
    except Agent.DoesNotExist as exc:
        raise AgentNotFoundError(str(agent_id)) from exc
    owner_id = agent.user_id
    agent.delete()
    publish_resource_update_after_commit(owner_id, 'agents')
