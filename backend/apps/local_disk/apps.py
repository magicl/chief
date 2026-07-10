# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django application configuration for local disk providers."""

from django.apps import AppConfig


class LocalDiskConfig(AppConfig):
    """Configure the local disk provider Django application."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.local_disk'
    label = 'local_disk'
