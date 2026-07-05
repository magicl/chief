# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery beat entrypoints for schedule and queue trigger dispatch."""

from __future__ import annotations

from celery import shared_task


@shared_task(ignore_result=True)
def dispatch_schedule_trigger(trigger_id: str) -> None:
    """Beat entry: start a session for one schedule trigger (PeriodicTask per cron)."""
    from apps.runner.scheduling import dispatch_schedule_trigger as cmd

    cmd(trigger_id=trigger_id)


@shared_task(ignore_result=True)
def dispatch_queue_triggers() -> None:
    """Beat wrapper: dispatch queue triggers for all queues with available items."""
    from apps.runner.scheduling import dispatch_queue_triggers as cmd

    cmd()


@shared_task(ignore_result=True)
def dispatch_queue_triggers_for_queue(queue_pk: str) -> None:
    """Enqueue dispatch for one queue (e.g. after put_item)."""
    from apps.runner.scheduling import dispatch_queue_triggers_for_queue as cmd

    cmd(queue_pk=queue_pk)
