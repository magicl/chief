# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django app declaration for local-provider reconciliation."""

from django.apps import AppConfig


class LocalSyncConfig(AppConfig):
    """Declare the cross-domain local provider reconciliation app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.local_sync'
