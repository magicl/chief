# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.apps import AppConfig
from django.db.models.signals import post_migrate


class WebConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.web'

    def ready(self) -> None:
        """Register safe local sync and start the process-local web watcher."""
        from apps.web.local_bootstrap import maybe_start_local_disk, sync_after_migrate

        post_migrate.connect(
            sync_after_migrate,
            dispatch_uid='apps.web.local_bootstrap.sync_after_migrate',
            weak=False,
        )
        maybe_start_local_disk(force_watch=True)
