# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Validate ``AgentConfigSpec`` and persist ``Agent`` + derived rows."""

from __future__ import annotations

from apps.agents.materialize import materialize_agent_config
from apps.agents.models import Agent, AgentConfig
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

# isort: split

from libs.agent_spec import AGENT_CONFIG_SPEC_VERSION, AgentConfigSpec, ToolInstance
from libs.tools.registry import get_tool

from olib.py.utils.uuid7 import uuid7


class IngestError(ValueError):
    """Spec or tool instance failed validation before persist."""


def validate_spec_tools(spec: AgentConfigSpec) -> None:
    """Ensure declared tool instances reference registered tools and functions."""
    for inst in spec.tools:
        _validate_tool_instance(inst)


def _validate_tool_instance(inst: ToolInstance) -> None:
    tool = get_tool(inst.type)
    if tool is None:
        raise IngestError(f'Unknown tool type {inst.type!r}')

    if inst.credential_ref and not getattr(tool, 'credential_type', None):
        raise IngestError(f"Tool {inst.type!r} does not accept credential_ref")

    known_functions = {fn.name for fn in tool.functions()}
    if '*' not in inst.allow:
        unknown = sorted(set(inst.allow) - known_functions)
        if unknown:
            raise IngestError(f'Tool {inst.type!r} has unknown allow entries: {", ".join(unknown)}')

    unknown_deny = sorted(set(inst.deny) - known_functions)
    if unknown_deny:
        raise IngestError(f'Tool {inst.type!r} has unknown deny entries: {", ".join(unknown_deny)}')


@transaction.atomic
def persist_agent_config(
    agent: Agent,
    spec: AgentConfigSpec,
    *,
    source_rev: str,
    dirty: bool = False,
) -> AgentConfig:
    """Validate spec and persist ``AgentConfig`` plus derived trigger rows."""
    validate_spec_tools(spec)
    if spec.schema_version != AGENT_CONFIG_SPEC_VERSION:
        raise IngestError('spec schema_version mismatch')

    config = AgentConfig.objects.create(
        agent=agent,
        source_rev=source_rev,
        dirty=dirty,
        spec_version=AGENT_CONFIG_SPEC_VERSION,
        spec=spec.model_dump(mode='json'),
    )

    materialize_agent_config(agent, config, spec)

    agent.current_config = config
    agent.save(update_fields=['current_config'])
    return config


@transaction.atomic
def create_agent_from_spec(
    user: AbstractBaseUser,
    spec: AgentConfigSpec,
    *,
    identifier: str | None = None,
    config_source: str = 'ui',
    source_rev: str = 'ui:initial',
) -> Agent:
    """Create ``Agent`` and persist config from a validated spec."""
    if identifier is None:
        identifier = str(uuid7())

    agent = Agent.objects.create(
        user_id=user.pk,
        identifier=identifier,
        config_source=config_source,
    )
    persist_agent_config(agent, spec, source_rev=source_rev)
    return agent
