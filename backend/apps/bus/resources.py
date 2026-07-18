# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""User-scoped resource update events for Redis pub/sub."""

from __future__ import annotations

import json
import logging
from typing import Literal

from apps.bus.client import key_prefix, sync_client
from django.db import transaction

ResourceName = Literal['agents', 'keys']
RESOURCE_NAMES: frozenset[ResourceName] = frozenset(('agents', 'keys'))
logger = logging.getLogger(__name__)


def user_resource_channel(user_id: int) -> str:
    """Return the cache-prefixed resource channel for a user."""
    return f'{key_prefix()}user:{user_id}:resources'


def resource_message(resource: ResourceName) -> dict[str, str]:
    """Validate a resource name and return its generic update envelope."""
    if resource not in RESOURCE_NAMES:
        raise ValueError(f'Unknown resource: {resource}')
    return {'channel': 'resource_update', 'resource': resource}


def publish_resource_update(user_id: int, resource: ResourceName) -> None:
    """Publish a resource update envelope to the user's channel."""
    sync_client().publish(user_resource_channel(user_id), json.dumps(resource_message(resource)))


def publish_resource_update_after_commit(user_id: int, resource: ResourceName) -> None:
    """Schedule a best-effort typed refresh hint after the write commits."""

    def publish() -> None:
        """Keep refresh transport failure independent from authoritative state."""
        try:
            publish_resource_update(user_id, resource)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug('Resource refresh transport unavailable')

    transaction.on_commit(publish, robust=True)
