# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Raw Redis clients for the event bus (not Django cache)."""

from __future__ import annotations

import redis
import redis.asyncio as aioredis
from django.conf import settings


def _redis_url() -> str | None:
    return getattr(settings, 'CELERY_WORKERS_BROKER_URL', None)


def sync_client() -> redis.Redis[str]:
    url = _redis_url()
    if not url:
        raise RuntimeError('REDIS_URL is not configured')
    return redis.Redis.from_url(url, decode_responses=True)


def async_client() -> aioredis.Redis[str]:
    url = _redis_url()
    if not url:
        raise RuntimeError('REDIS_URL is not configured')
    return aioredis.Redis.from_url(url, decode_responses=True)


def key_prefix() -> str:
    return getattr(settings, 'CACHE_PREFIX', '')
