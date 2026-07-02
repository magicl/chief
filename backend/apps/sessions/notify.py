# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Session-scoped Redis notification envelopes."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from apps.bus.channels import publish_session_message

SessionChannel = Literal['session_event', 'session_update']


def session_message(channel: SessionChannel, payload: dict[str, Any]) -> dict[str, Any]:
    return {'channel': channel, 'payload': payload}


def publish_session_event(session_id: UUID | str, event_dict: dict[str, Any]) -> None:
    publish_session_message(session_id, session_message('session_event', event_dict))


def publish_session_update(session_id: UUID | str, patch: dict[str, Any]) -> None:
    publish_session_message(session_id, session_message('session_update', patch))
