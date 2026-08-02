# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Materialize derived rows from a persisted agent config spec."""

from __future__ import annotations

from uuid import UUID

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


def _notify_lifecycle_materialized(agent_id: UUID, user_id: int, spec: AgentConfigSpec) -> None:
    """Dispatch registered materialize handlers after commit (agent must still exist)."""
    from apps.agents.lifecycle import notify_agent_materialized

    if not Agent.objects.filter(pk=agent_id).exists():
        return
    notify_agent_materialized(agent_id, user_id, spec)


def materialize_agent_config(agent: Agent, config: AgentConfig, spec: AgentConfigSpec) -> None:
    """Sync derived runtime rows (triggers, queues, sources) from *spec* after config save."""
    _sync_triggers(agent, config, spec.triggers)
    from apps.queues.services import commands as queue_commands

    queue_commands.sync_from_spec(agent, config, spec.queues)

    from apps.agents.services.schedule_beat import sync_agent_schedule_triggers
    from django.db import transaction

    agent_id = agent.id
    user_id = agent.user_id
    transaction.on_commit(lambda: sync_agent_schedule_triggers(agent_id))
    transaction.on_commit(lambda: _notify_lifecycle_materialized(agent_id, user_id, spec))
