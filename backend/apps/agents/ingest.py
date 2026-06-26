# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Validate ``AgentConfigSpec`` and persist ``Agent`` + derived rows."""

from __future__ import annotations

from apps.agents.models import Agent, AgentConfig, Trigger, TriggerStatus
from apps.agents.spec import AgentConfigSpec, ToolPermission
from apps.agents.tools.registry import get_tool
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from olib.py.utils.uuid7 import uuid7


class IngestError(ValueError):
    """Spec or tool permission failed validation before persist."""


def validate_spec_tools(spec: AgentConfigSpec) -> None:
    """Ensure declared tool permissions reference registered tools and functions."""
    for perm in spec.tools:
        _validate_tool_permission(perm)


def _validate_tool_permission(perm: ToolPermission) -> None:
    tool = get_tool(perm.tool)
    if tool is None:
        raise IngestError(f'Unknown tool {perm.tool!r}')

    known_functions = {fn.name for fn in tool.functions()}
    if '*' not in perm.allow:
        unknown = sorted(set(perm.allow) - known_functions)
        if unknown:
            raise IngestError(f'Tool {perm.tool!r} has unknown allow entries: {", ".join(unknown)}')

    unknown_deny = sorted(set(perm.deny) - known_functions)
    if unknown_deny:
        raise IngestError(f'Tool {perm.tool!r} has unknown deny entries: {", ".join(unknown_deny)}')


@transaction.atomic
def create_agent_from_spec(
    user: AbstractBaseUser,
    spec: AgentConfigSpec,
    *,
    identifier: str | None = None,
    config_source: str = 'hardcoded',
    source_rev: str = 'hardcoded-v0.1',
) -> Agent:
    """Create ``Agent``, ``AgentConfig``, and ``Trigger`` rows from a validated spec."""
    validate_spec_tools(spec)

    if identifier is None:
        identifier = str(uuid7())

    agent = Agent.objects.create(
        user_id=user.pk,
        identifier=identifier,
        config_source=config_source,
    )

    spec_json = spec.model_dump(mode='json')
    config = AgentConfig.objects.create(
        agent=agent,
        source_rev=source_rev,
        dirty=False,
        fetched_at=timezone.now(),
        spec=spec_json,
    )

    for trigger_spec in spec.triggers:
        Trigger.objects.create(
            agent=agent,
            agent_config=config,
            name=trigger_spec.name,
            kind=trigger_spec.kind,
            status=TriggerStatus.ACTIVE,
            spec=trigger_spec.model_dump(mode='json'),
        )

    agent.current_config = config
    agent.save(update_fields=['current_config'])
    return agent
