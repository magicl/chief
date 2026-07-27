# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.apps import AppConfig


class SessionsConfig(AppConfig):
    """Configure session models and deletion reconciliation signals."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.sessions'
    label = 'agent_sessions'

    def ready(self) -> None:
        """Register model observers after Django loads the app registry."""
        from apps.sessions import signals

        del signals
