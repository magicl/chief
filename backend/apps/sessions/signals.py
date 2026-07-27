# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session deletion observers preserving linked parent activity state."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.sessions.models import AgentSession, AgentSessionActivity
from django.db import transaction
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver


@receiver(pre_delete, sender=AgentSession)
def remember_child_reference(
    sender: type[AgentSession],
    instance: AgentSession,
    *,
    origin: Any,
    **kwargs: Any,
) -> None:
    """Lock and freeze any linked child reference before collector SQL runs."""
    del sender, origin, kwargs
    if instance.parent_session_id is None:
        return
    activity_id = (
        AgentSessionActivity.objects.select_for_update()
        .filter(child_session_id=instance.id)
        .values_list('id', flat=True)
        .first()
    )
    if activity_id is not None:
        setattr(instance, 'deleted_subagent_activity_id_for_signal', activity_id)


@receiver(post_delete, sender=AgentSession)
def schedule_child_reference_reconciliation(
    sender: type[AgentSession],
    instance: AgentSession,
    **kwargs: Any,
) -> None:
    """Reconcile a surviving reference only after the full collector commits."""
    del sender, kwargs
    activity_id: UUID | None = getattr(instance, 'deleted_subagent_activity_id_for_signal', None)
    if activity_id is None:
        return
    from apps.sessions.services.commands import reconcile_deleted_subagent_reference

    child_id = instance.id

    def reconcile_after_collector() -> None:
        """Finalize only references that survived the committed delete graph."""
        reconcile_deleted_subagent_reference(
            activity_id,
            prior_child_session_id=child_id,
        )

    transaction.on_commit(reconcile_after_collector)
