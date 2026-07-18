# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery entry point for finite leased local-provider reconciliation."""

from __future__ import annotations

import logging
import uuid

from apps.bus.leases import release_lease, renew_lease, try_acquire_lease
from apps.local_sync.reconcile import reconcile_local_providers, resolve_local_root
from celery import shared_task

logger = logging.getLogger(__name__)

LOCAL_SYNC_LEASE_NAME = 'local-provider-sync'
LOCAL_SYNC_LEASE_TTL_SECONDS = 30


@shared_task(name='apps.local_sync.tasks.reconcile_local_providers', ignore_result=True)
def reconcile_local_providers_task() -> None:
    """Run one leased scan and rely on the next Beat tick after failure."""
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return

    owner_token = str(uuid.uuid4())
    if not try_acquire_lease(
        LOCAL_SYNC_LEASE_NAME,
        owner_token,
        ttl_seconds=LOCAL_SYNC_LEASE_TTL_SECONDS,
    ):
        return

    def maintain_lease() -> None:
        """Renew the token-owned lease at a finite reconciliation checkpoint."""
        if not renew_lease(
            LOCAL_SYNC_LEASE_NAME,
            owner_token,
            ttl_seconds=LOCAL_SYNC_LEASE_TTL_SECONDS,
        ):
            raise RuntimeError('local provider lease ownership lost')

    try:
        reconcile_local_providers(root=root, progress=maintain_lease)
    except Exception:  # pylint: disable=broad-exception-caught
        # This task boundary contains one run; per-file failures remain domain-owned.
        logger.exception('Local provider reconciliation failed')
    finally:
        # Compare-and-delete prevents a stale owner from releasing a successor's lease.
        release_lease(LOCAL_SYNC_LEASE_NAME, owner_token)
