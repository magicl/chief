# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for chief Celery app configuration."""

from __future__ import annotations

from chief.celery import app

from olib.py.django.test.cases import OTestCase


class TestCeleryBeatScheduler(OTestCase):
    def test_beat_scheduler_uses_database_scheduler(self) -> None:
        """Schedule triggers rely on django-celery-beat PeriodicTask rows in the DB."""
        self.assertEqual(
            app.conf.beat_scheduler,
            'django_celery_beat.schedulers:DatabaseScheduler',
        )

    def test_local_sync_schedule_runs_exact_task_every_five_seconds(self) -> None:
        """Configure one exact finite local-provider task on the Beat cadence."""
        schedule = app.conf.beat_schedule['local-sync-reconcile']

        self.assertEqual(schedule['task'], 'apps.local_sync.tasks.reconcile_local_providers')
        self.assertEqual(schedule['schedule'], 5.0)
        self.assertEqual(schedule['options'], {'expires': 5.0})
