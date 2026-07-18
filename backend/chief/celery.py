# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery app for chief. Workers run agents and periodic monitors."""

from django.conf import settings

from olib.py.django.celery_workers import initCelery

app = initCelery('chief')

app.conf.beat_scheduler = settings.CELERY_BEAT_SCHEDULER

app.conf.beat_schedule = {
    'local-sync-reconcile': {
        'task': 'apps.local_sync.tasks.reconcile_local_providers',
        'schedule': 5.0,
        'options': {'expires': 5.0},
    },
    'queues-release-stale-items': {
        'task': 'apps.queues.tasks.release_stale_items',
        'schedule': 120.0,
    },
    'queues-poll-active-sources': {
        'task': 'apps.queues.tasks.poll_active_sources',
        'schedule': 300.0,
    },
    'runner-dispatch-queue-triggers': {
        'task': 'apps.runner.trigger_tasks.dispatch_queue_triggers',
        'schedule': 15.0,
    },
    'sessions-aggregate-hourly-usage': {
        'task': 'apps.sessions.tasks.aggregate_hourly_usage',
        'schedule': 600.0,  # every 10 minutes
    },
}
