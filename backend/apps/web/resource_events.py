# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Authenticated SSE transport for user-scoped resource refresh hints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any, Protocol, cast
from uuid import UUID

from apps.bus.client import async_client
from apps.bus.resources import RESOURCE_NAMES, user_resource_channel
from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.db import connections
from django.http import Http404, HttpRequest, StreamingHttpResponse
from django.views.decorators.http import require_GET
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)
RESOURCE_SSE_POLL_SECONDS = 1.0
RESOURCE_SSE_HEARTBEAT_SECONDS = 15.0


class _AsyncClosable(Protocol):
    """Describe Redis 7.3 async close while upstream type stubs lag."""

    async def aclose(self) -> None:
        """Close an async Redis resource."""


def _require_authenticated_user_id(request: HttpRequest) -> int:
    """Read the authenticated user id exclusively from the request session."""
    if not request.user.is_authenticated:
        raise Http404('Not found')
    return int(cast(AbstractBaseUser, request.user).pk)


def _valid_uuid_string(value: Any) -> str | None:
    """Return *value* unchanged when it is a syntactically valid UUID string, else None."""
    if not isinstance(value, str):
        return None
    try:
        UUID(value)
    except ValueError:
        return None
    return value


def _validated_resource_message(data: Any) -> dict[str, str] | None:
    """Validate and canonicalize the public envelope without retaining extra data."""
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        logger.debug('Skipping malformed resource refresh message')
        return None
    if not isinstance(raw, dict) or raw.get('channel') != 'resource_update':
        logger.debug('Skipping unknown resource refresh message')
        return None
    resource = raw.get('resource')
    if not isinstance(resource, str) or resource not in RESOURCE_NAMES:
        logger.debug('Skipping unknown resource refresh message')
        return None
    message: dict[str, str] = {'channel': 'resource_update', 'resource': resource}
    agent_id = _valid_uuid_string(raw.get('agent_id'))
    if agent_id is not None:
        message['agent_id'] = agent_id
    queue_id = _valid_uuid_string(raw.get('queue_id'))
    if queue_id is not None:
        message['queue_id'] = queue_id
    return message


@require_GET
@login_required(login_url='/admin/login/')
async def resource_events_sse(request: HttpRequest) -> StreamingHttpResponse:
    """Tail only the authenticated user's resource refresh channel."""
    user_id = await sync_to_async(_require_authenticated_user_id)(request)
    # Streaming responses delay request_finished indefinitely, so explicitly
    # return authentication connections before the Redis-only phase.
    await sync_to_async(connections.close_all)()

    async def stream() -> AsyncIterator[str]:
        """Subscribe lazily and release each Redis resource on disconnect."""
        # Response middleware runs after the view-level close and may touch the
        # database, so release once more at the actual long-lived boundary.
        await sync_to_async(connections.close_all)()
        try:
            client = async_client()
        except RuntimeError:
            return

        try:
            pubsub = client.pubsub()
            try:
                channel = user_resource_channel(user_id)
                subscribed = False
                try:
                    try:
                        await pubsub.subscribe(channel)
                        subscribed = True
                    except (RedisConnectionError, RedisTimeoutError):
                        logger.debug('Resource refresh subscription unavailable')
                        return
                    last_heartbeat = monotonic()
                    while True:
                        try:
                            message = await pubsub.get_message(
                                ignore_subscribe_messages=True,
                                timeout=RESOURCE_SSE_POLL_SECONDS,
                            )
                        except (RedisConnectionError, RedisTimeoutError):
                            logger.debug('Resource refresh stream unavailable')
                            return
                        if message is None:
                            now = monotonic()
                            if now - last_heartbeat >= RESOURCE_SSE_HEARTBEAT_SECONDS:
                                yield ': heartbeat\n\n'
                                last_heartbeat = now
                            await asyncio.sleep(0.1)
                            continue
                        if message.get('type') != 'message':
                            continue
                        raw = _validated_resource_message(message.get('data'))
                        if raw is None:
                            continue
                        yield f'event: resource_update\ndata: {json.dumps(raw)}\n\n'
                finally:
                    if subscribed:
                        try:
                            await pubsub.unsubscribe(channel)
                        except (RedisConnectionError, RedisTimeoutError):
                            logger.debug('Resource refresh unsubscribe unavailable')
            finally:
                try:
                    await cast(_AsyncClosable, pubsub).aclose()
                except (RedisConnectionError, RedisTimeoutError):
                    logger.debug('Resource refresh pubsub close unavailable')
        finally:
            try:
                await cast(_AsyncClosable, client).aclose()
            except (RedisConnectionError, RedisTimeoutError):
                logger.debug('Resource refresh client close unavailable')

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
