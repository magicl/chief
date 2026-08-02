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


def _sync_obsidian_vaults_on_commit(agent_id: UUID, spec: AgentConfigSpec) -> None:
    """Reload the agent by id post-commit and ensure its obsidian vault bindings.

    Reloading by id (rather than closing over the `agent` row materialize already
    holds) avoids depending on that ORM instance staying fresh/unmutated until the
    callback fires after commit — mirroring `sync_agent_schedule_triggers`, which
    takes only `agent.id` for the same reason.
    """
    from apps.agents.vault_lifecycle import sync_obsidian_vaults

    agent = Agent.objects.filter(pk=agent_id).first()
    if agent is None:
        return
    sync_obsidian_vaults(agent, spec)


def materialize_agent_config(agent: Agent, config: AgentConfig, spec: AgentConfigSpec) -> None:
    """Sync derived runtime rows (triggers, queues, sources) from *spec* after config save."""
    _sync_triggers(agent, config, spec.triggers)
    from apps.queues.services import commands as queue_commands

    queue_commands.sync_from_spec(agent, config, spec.queues)

    from apps.agents.services.schedule_beat import sync_agent_schedule_triggers
    from django.db import transaction

    transaction.on_commit(lambda: sync_agent_schedule_triggers(agent.id))
    transaction.on_commit(lambda: _sync_obsidian_vaults_on_commit(agent.id, spec))
