# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Per-session pub/sub and mailbox primitives.

Session locks (``try_acquire_lock`` / ``release_lock`` / ``is_locked``) enforce
a single active Celery runner per session. The ``run_session`` task acquires the
lock at start and releases it in ``finally``; ``dispatch_session`` skips enqueue
when ``is_locked`` is true so resume/chat cannot start a duplicate worker.

Locks are **not** used for web requests or SSE — only for runner task
exclusivity and the single-writer event log invariant. ``refresh_lock`` exists
for future heartbeat extension of long runs but is not wired yet.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from apps.bus.client import key_prefix, sync_client

LOCK_TTL_SECONDS = 300
LOCK_HEARTBEAT_SECONDS = 60


def _session_key(session_id: UUID | str, suffix: str) -> str:
    return f'{key_prefix()}session:{session_id}:{suffix}'


def publish_event(session_id: UUID | str, event_dict: dict[str, Any]) -> None:
    client = sync_client()
    channel = _session_key(session_id, 'events')
    client.publish(channel, json.dumps(event_dict))


def mailbox_push(session_id: UUID | str, message: dict[str, Any]) -> None:
    client = sync_client()
    key = _session_key(session_id, 'mailbox')
    client.rpush(key, json.dumps(message))


def mailbox_drain(session_id: UUID | str) -> list[dict[str, Any]]:
    client = sync_client()
    key = _session_key(session_id, 'mailbox')
    raw_items = client.lrange(key, 0, -1)
    if raw_items:
        client.delete(key)
    return [json.loads(item) for item in raw_items]


def try_acquire_lock(session_id: UUID | str, token: str, *, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """Acquire the per-session runner lock (see module docstring)."""
    client = sync_client()
    key = _session_key(session_id, 'lock')
    return bool(client.set(key, token, nx=True, ex=ttl))


def refresh_lock(session_id: UUID | str, token: str, *, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """Extend lock TTL when the holder token matches (heartbeat hook, not wired yet)."""
    client = sync_client()
    key = _session_key(session_id, 'lock')
    current = client.get(key)
    if current != token:
        return False
    client.expire(key, ttl)
    return True


def release_lock(session_id: UUID | str, token: str) -> None:
    """Release the runner lock if the caller still holds it."""
    client = sync_client()
    key = _session_key(session_id, 'lock')
    current = client.get(key)
    if current == token:
        client.delete(key)


def is_locked(session_id: UUID | str) -> bool:
    """True when a runner task currently holds the session lock."""
    client = sync_client()
    key = _session_key(session_id, 'lock')
    return client.exists(key) > 0
