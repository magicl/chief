# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.apps import AppConfig
from django.conf import settings


class RunnerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.runner'

    def ready(self) -> None:
        """Opt worker processes into local disk sync when watching is enabled."""
        # Workers normally consume the web watcher's DB updates; this supports
        # deployments that run a worker without a web process.
        if getattr(settings, 'CHIEF_LOCAL_WATCH', False):
            from apps.web.local_bootstrap import maybe_start_local_disk

            maybe_start_local_disk()
