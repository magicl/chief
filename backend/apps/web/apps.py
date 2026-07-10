# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.web'

    def ready(self) -> None:
        """Synchronize and watch configured local disk providers in web processes."""
        from apps.local_disk.bootstrap import maybe_start_local_disk

        maybe_start_local_disk(force_watch=True)
