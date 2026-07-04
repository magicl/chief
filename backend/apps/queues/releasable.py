# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session predicates for stale queue item release."""

from __future__ import annotations

from apps.sessions.models import AgentSession, AgentSessionStatus


def is_session_releasable(session: AgentSession) -> bool:
    """True when the session finished work and a held queue item may be released early."""
    return session.status in {AgentSessionStatus.DONE, AgentSessionStatus.WAITING} or session.ended_at is not None
