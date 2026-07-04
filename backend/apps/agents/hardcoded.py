# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Hardcoded v0.1 demo spec and bootstrap helper."""

from __future__ import annotations

from apps.agents.ingest import create_agent_from_spec
from apps.agents.models import Agent
from apps.agents.spec import AgentConfigSpec, LLMSpec, ToolInstance, TriggerSpec
from django.contrib.auth.models import AbstractBaseUser

HARDCODED_SPEC = AgentConfigSpec(
    schema_version=1,
    description='v0.1 demo agent',
    llm=LLMSpec(provider='openai', model='gpt-5.4-mini', temperature=0.7),
    system_prompt=(
        'You are a helpful assistant running inside Chief. '
        'You can check the current UTC time using the clock tool when asked. '
        'Keep responses concise.'
    ),
    triggers=[TriggerSpec(name='manual', kind='manual')],
    tools=[ToolInstance(id='clock', type='clock', allow=['now'])],
)


def demo_agent_spec(*, provider: str, model: str) -> AgentConfigSpec:
    """Build the v0.1 demo spec for a chosen provider/model."""
    return HARDCODED_SPEC.model_copy(
        update={
            'llm': LLMSpec(
                provider=provider,
                model=model,
                temperature=HARDCODED_SPEC.llm.temperature,
            ),
        }
    )


def bootstrap_agent(
    user: AbstractBaseUser,
    *,
    identifier: str | None = None,
    provider: str,
    model: str,
) -> Agent:
    """Create a demo agent from the v0.1 hardcoded template."""
    return create_agent_from_spec(
        user,
        demo_agent_spec(provider=provider, model=model),
        identifier=identifier,
        config_source='hardcoded',
        source_rev='hardcoded-v0.1',
    )
