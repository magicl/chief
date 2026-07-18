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
from typing import Any, Protocol, cast

from apps.bus.client import async_client
from apps.bus.resources import RESOURCE_NAMES, user_resource_channel
from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.http import Http404, HttpRequest, StreamingHttpResponse
from django.views.decorators.http import require_GET
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)


class _AsyncClosable(Protocol):
    """Describe Redis 7.3 async close while upstream type stubs lag."""

    async def aclose(self) -> None:
        """Close an async Redis resource."""


def _require_authenticated_user_id(request: HttpRequest) -> int:
    """Read the authenticated user id exclusively from the request session."""
    if not request.user.is_authenticated:
        raise Http404('Not found')
    return int(cast(AbstractBaseUser, request.user).pk)


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
    return {'channel': 'resource_update', 'resource': resource}


@require_GET
@login_required(login_url='/admin/login/')
async def resource_events_sse(request: HttpRequest) -> StreamingHttpResponse:
    """Tail only the authenticated user's resource refresh channel."""
    user_id = await sync_to_async(_require_authenticated_user_id)(request)

    async def stream() -> AsyncIterator[str]:
        """Subscribe lazily and release each Redis resource on disconnect."""
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
                    while True:
                        try:
                            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                        except (RedisConnectionError, RedisTimeoutError):
                            logger.debug('Resource refresh stream unavailable')
                            return
                        if message is None:
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
