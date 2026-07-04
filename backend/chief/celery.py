# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery app for chief. Workers run agents and periodic monitors."""

from olib.py.django.celery_workers import initCelery

app = initCelery('chief')

app.conf.beat_schedule = {
    'queues-release-stale-items': {
        'task': 'apps.queues.tasks.release_stale_items',
        'schedule': 120.0,
    },
}
