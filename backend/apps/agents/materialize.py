# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Materialize derived rows from a persisted agent config spec."""

from __future__ import annotations

from apps.agents.models import Agent, AgentConfig, Trigger, TriggerStatus
from libs.agent_spec import AgentConfigSpec, TriggerSpec


def _sync_triggers(agent: Agent, config: AgentConfig, triggers: list[TriggerSpec]) -> None:
    """Create ``Trigger`` rows for each entry in a newly persisted config revision."""
    for trigger_spec in triggers:
        Trigger.objects.create(
            agent=agent,
            agent_config=config,
            name=trigger_spec.name,
            kind=trigger_spec.kind,
            status=TriggerStatus.ACTIVE,
            spec=trigger_spec.model_dump(mode='json'),
        )


def materialize_agent_config(agent: Agent, config: AgentConfig, spec: AgentConfigSpec) -> None:
    """Sync derived runtime rows (triggers, queues, sources) from *spec* after config save."""
    _sync_triggers(agent, config, spec.triggers)
    from apps.queues.services import commands as queue_commands

    queue_commands.sync_from_spec(agent, config, spec.queues)
