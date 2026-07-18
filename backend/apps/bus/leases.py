# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Redis lease primitives with atomic ownership enforcement.

A lease may be released only when its stored token still matches the caller's
token. The comparison and deletion must remain one atomic Redis operation so a
stale owner can never remove a replacement lease.
"""

from __future__ import annotations

from apps.bus.client import key_prefix, sync_client

_RELEASE_LEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_LEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def lease_key(name: str) -> str:
    """Return the cache-prefixed Redis key for a named lease."""
    return f'{key_prefix()}lease:{name}'


def try_acquire_lease(name: str, token: str, *, ttl_seconds: int) -> bool:
    """Acquire a named lease for the token when no owner exists."""
    return bool(sync_client().set(lease_key(name), token, nx=True, ex=ttl_seconds))


def renew_lease(name: str, token: str, *, ttl_seconds: int) -> bool:
    """Atomically extend a named lease only while the token still owns it."""
    result = sync_client().eval(  # type: ignore[no-untyped-call]
        _RENEW_LEASE_SCRIPT,
        1,
        lease_key(name),
        token,
        ttl_seconds,
    )
    return bool(result)


def release_lease(name: str, token: str) -> bool:
    """Atomically release a named lease only when the token still owns it."""
    # redis-py does not type its eval wrapper, but Redis returns the Lua integer result here.
    result = sync_client().eval(_RELEASE_LEASE_SCRIPT, 1, lease_key(name), token)  # type: ignore[no-untyped-call]
    return bool(result)
