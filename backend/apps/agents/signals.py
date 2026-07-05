# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent model signals for derived runtime sync."""

from __future__ import annotations

from apps.agents.models import Trigger, TriggerKind
from apps.agents.services.schedule_beat import disable_schedule_trigger_beat, sync_schedule_trigger
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver(post_save, sender=Trigger)
def sync_schedule_trigger_beat_on_save(sender: type[Trigger], instance: Trigger, **kwargs: object) -> None:
    """Keep django-celery-beat rows aligned when a schedule trigger row changes."""
    del sender, kwargs
    if instance.kind != TriggerKind.SCHEDULE:
        return
    trigger_id = instance.id
    transaction.on_commit(lambda: sync_schedule_trigger(trigger_id))


@receiver(post_delete, sender=Trigger)
def disable_schedule_trigger_beat_on_delete(sender: type[Trigger], instance: Trigger, **kwargs: object) -> None:
    """Disable PeriodicTask rows when a schedule trigger row is deleted."""
    del sender, kwargs
    if instance.kind != TriggerKind.SCHEDULE:
        return
    trigger_id = instance.id
    transaction.on_commit(lambda: disable_schedule_trigger_beat(trigger_id))
