# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pull Chief's authoritative vault-binding snapshot and apply it locally.

On vault-service startup the in-memory binding map is empty even though checkout
trees may still exist on the data volume. This module fetches the full snapshot
from Chief's internal HTTP API (same inter-service bearer token), replaces the
local map atomically, and starts/stops supervisors accordingly. Credentials are
never persisted by the vault service — only held in memory after each pull or
incremental ensure.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from obsidian_vault.bindings import VaultBindingStore

logger = logging.getLogger(__name__)


class VaultSupervisor(Protocol):
    """Minimal supervisor surface needed to start/stop vaults after reconcile."""

    def ensure_vault(
        self,
        vault_id: str,
        *,
        auth_token: str,
        encryption_password: str | None,
    ) -> None:
        """Start or reuse headless sync for vault_id."""

    def stop_vault(self, vault_id: str) -> None:
        """Stop headless sync for vault_id."""

    def is_initial_sync_complete(self, vault_id: str) -> bool:
        """Return True when first full sync has finished for vault_id."""


def fetch_bindings_snapshot(
    *,
    chief_internal_url: str,
    token: str,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """GET Chief's vault-bindings snapshot; return the `agents` list.

    Raises httpx exceptions / ValueError on transport or payload failures.
    """
    base = chief_internal_url.rstrip('/')
    url = f'{base}/internal/obsidian/vault-bindings/'
    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=30.0)
    try:
        response = http.get(url, headers={'Authorization': f'Bearer {token}'})
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            http.close()
    if not isinstance(payload, dict) or payload.get('ok') is not True:
        raise ValueError(f'Unexpected vault-bindings snapshot payload: {payload!r}')
    agents = payload.get('agents')
    if not isinstance(agents, list):
        raise ValueError('vault-bindings snapshot missing agents list')
    return agents


def apply_bindings_snapshot(
    store: VaultBindingStore,
    supervisor: VaultSupervisor,
    agents: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Replace local bindings from `agents` and align supervisors with the result.

    Returns ``(needs_start, released)`` from the store for tests/observability.
    Supervisor starts own single-flight and run without the store's file lock;
    release still rechecks references while holding that lock before stopping.
    """
    credentials_by_vault: dict[str, dict[str, Any]] = {}
    for entry in agents:
        for binding in entry.get('bindings', []):
            vault_id = binding['vault_id']
            credentials_by_vault[vault_id] = binding.get('credential', {})

    needs_start, released = store.replace_all_agents(agents)

    for vault_id in released:
        with store.lock_for(vault_id):
            if not store.has_references(vault_id):
                supervisor.stop_vault(vault_id)

    for vault_id in needs_start:
        credential = credentials_by_vault.get(vault_id, {})
        auth_token = credential.get('auth_token')
        if not isinstance(auth_token, str) or not auth_token:
            logger.warning('snapshot vault %r missing auth_token; skipping supervisor start', vault_id)
            continue
        encryption_password = credential.get('encryption_password')
        if encryption_password is not None and not isinstance(encryption_password, str):
            encryption_password = None
        # Supervisor single-flight owns start serialization without holding
        # the store's file lock across potentially long subprocess waits.
        supervisor.ensure_vault(
            vault_id,
            auth_token=auth_token,
            encryption_password=encryption_password,
        )
        with store.lock_for(vault_id):
            if store.has_references(vault_id) and supervisor.is_initial_sync_complete(vault_id):
                store.mark_vault_ready(vault_id)

    return needs_start, released


def reconcile_bindings_from_chief(
    *,
    store: VaultBindingStore,
    supervisor: VaultSupervisor,
    chief_internal_url: str,
    token: str,
    sleep: Callable[[float], None] = time.sleep,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
) -> None:
    """Fetch and apply the Chief snapshot, retrying until success.

    Blocks the caller (intended for ASGI lifespan startup) so the vault service
    does not serve file ops against an empty binding map after restart.
    """
    delay = initial_delay
    while True:
        try:
            agents = fetch_bindings_snapshot(chief_internal_url=chief_internal_url, token=token)
            apply_bindings_snapshot(store, supervisor, agents)
            logger.info('Reconciled %d agent vault binding(s) from Chief', len(agents))
            return
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.warning(
                'Vault binding reconcile from Chief failed; retrying in %.1fs',
                delay,
                exc_info=True,
            )
            sleep(delay)
            delay = min(delay * 2.0, max_delay)
