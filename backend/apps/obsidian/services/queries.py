# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read-only Obsidian vault binding queries for the vault-service snapshot API."""

from __future__ import annotations

from typing import Any

from apps.agents.models import Agent, AgentStatus
from apps.obsidian.lifecycle import bindings_for_spec


def build_vault_bindings_snapshot() -> list[dict[str, Any]]:
    """Return every active agent's current ``obsidian`` bindings with resolved secrets.

    Used by the vault service on startup to rebuild its in-memory map. Agents
    without a current config or without any valid ``obsidian`` bindings are omitted.
    """
    agents: list[dict[str, Any]] = []
    queryset = (
        Agent.objects.filter(status=AgentStatus.ACTIVE, current_config__isnull=False)
        .select_related('current_config')
        .order_by('id')
    )
    for agent in queryset:
        config = agent.current_config
        if config is None:
            continue
        bindings = bindings_for_spec(agent, config.get_spec())
        if not bindings:
            continue
        agents.append({'agent_id': str(agent.id), 'bindings': bindings})
    return agents
