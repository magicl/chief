# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""User-scoped resource update events for Redis pub/sub."""

from __future__ import annotations

import json
import logging
from typing import Literal
from uuid import UUID

from apps.bus.client import key_prefix, sync_client
from django.db import transaction

ResourceName = Literal['agents', 'keys', 'queues']
RESOURCE_NAMES: frozenset[ResourceName] = frozenset(('agents', 'keys', 'queues'))
logger = logging.getLogger(__name__)


def user_resource_channel(user_id: int) -> str:
    """Return the cache-prefixed resource channel for a user."""
    return f'{key_prefix()}user:{user_id}:resources'


def resource_message(
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> dict[str, str]:
    """Validate a resource name and return its update envelope.

    ``agent_id`` / ``queue_id`` are optional scoping hints (currently used by the
    ``queues`` resource). Omitted values are left out of the envelope entirely
    (never sent as null), so unscoped calls produce the same minimal shape as
    before this scoping was added.
    """
    if resource not in RESOURCE_NAMES:
        raise ValueError(f'Unknown resource: {resource}')
    message: dict[str, str] = {'channel': 'resource_update', 'resource': resource}
    if agent_id is not None:
        message['agent_id'] = str(agent_id)
    if queue_id is not None:
        message['queue_id'] = str(queue_id)
    return message


def publish_resource_update(
    user_id: int,
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> None:
    """Publish a resource update envelope to the user's channel."""
    sync_client().publish(
        user_resource_channel(user_id),
        json.dumps(resource_message(resource, agent_id=agent_id, queue_id=queue_id)),
    )


def publish_resource_update_after_commit(
    user_id: int,
    resource: ResourceName,
    *,
    agent_id: UUID | str | None = None,
    queue_id: UUID | str | None = None,
) -> None:
    """Schedule a best-effort typed refresh hint after the write commits."""

    def publish() -> None:
        """Keep refresh transport failure independent from authoritative state.

        Forwards agent_id/queue_id only when the caller supplied them, so unscoped
        calls (agents/keys, and existing tests asserting the two-positional-arg
        call shape) keep publishing with the original minimal signature.
        """
        scoped_kwargs: dict[str, UUID | str] = {}
        if agent_id is not None:
            scoped_kwargs['agent_id'] = agent_id
        if queue_id is not None:
            scoped_kwargs['queue_id'] = queue_id
        try:
            publish_resource_update(user_id, resource, **scoped_kwargs)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug('Resource refresh transport unavailable')

    transaction.on_commit(publish, robust=True)
