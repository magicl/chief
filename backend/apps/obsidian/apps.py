# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django app config for Obsidian vault lifecycle + internal snapshot API."""

from __future__ import annotations

from django.apps import AppConfig


class ObsidianConfig(AppConfig):
    """Register Obsidian handlers against the generic agent lifecycle registry."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.obsidian'
    label = 'obsidian'

    def ready(self) -> None:
        """Wire vault ensure/release into agent materialize/delete notifications."""
        from apps.agents.lifecycle import (
            register_agent_deleted_handler,
            register_agent_materialized_handler,
        )
        from apps.obsidian.lifecycle import on_agent_deleted, on_agent_materialized

        register_agent_materialized_handler(on_agent_materialized)
        register_agent_deleted_handler(on_agent_deleted)
