# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Post-run session status adjustments for automated triggers."""

from __future__ import annotations

from apps.agents.models import Trigger, TriggerKind
from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from django.utils import timezone

_AUTOMATED_TERMINATE_KINDS = frozenset({TriggerKind.SCHEDULE, TriggerKind.QUEUE})


def finalize_automated_trigger_session(session: AgentSession) -> None:
    """End schedule/queue trigger sessions at waiting so they release capacity slots."""
    if session.status != AgentSessionStatus.WAITING:
        return
    if session.trigger_type != TriggerType.TRIGGER or session.trigger_ref is None:
        return
    try:
        trigger = Trigger.objects.get(pk=session.trigger_ref)
    except Trigger.DoesNotExist:
        return
    if trigger.kind not in _AUTOMATED_TERMINATE_KINDS:
        return
    session.status = AgentSessionStatus.DONE
    session.ended_at = timezone.now()
    session.save(update_fields=['status', 'ended_at'])
