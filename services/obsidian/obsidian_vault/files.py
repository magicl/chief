# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Path-gated, sync-state-gated filesystem file operations for vaults.

`VaultFileService` is the only place file IO happens for the vault service.
Reads accept an intentionally incomplete or changing checkout once supervisor
sync has started. Writes additionally require completed first-sync readiness
and serialize IO with the per-vault file lock. Every path uses O_NOFOLLOW
descriptor walks so an agent cannot escape its configured roots.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from obsidian_vault.bindings import SyncPendingError, VaultBindingStore
from obsidian_vault.paths import list_dir_under_roots, open_file_under_roots
from obsidian_vault.supervisor import VaultSyncState


class VaultUnavailableError(Exception):
    """Raised when a hard first-sync failure makes a checkout unsafe to use."""


class VaultFileService:
    """List/read/write/append operations for a single vault's working tree.

    `vault_root_for` maps a `vault_id` to the filesystem root of its
    checkout; this class does not know how that checkout was produced
    (headless Sync, tests, etc.) and never touches paths outside what
    the caller's binding roots allow.
    """

    def __init__(
        self,
        store: VaultBindingStore,
        vault_root_for: Callable[[str], Path],
        sync_state_for: Callable[[str], VaultSyncState],
    ) -> None:
        """Store binding, checkout-path, and first-sync-state collaborators.

        `sync_state_for` must return promptly without waiting on an `ob`
        subprocess so partial reads stay available during first sync.
        """
        self._store = store
        self._vault_root_for = vault_root_for
        self._sync_state_for = sync_state_for

    def _binding_context(
        self,
        agent_id: str,
        vault_id: str,
        *,
        require_ready: bool,
    ) -> tuple[Path, list[str]]:
        """Resolve binding first, then gate sync state and optional write readiness.

        The supervisor callback must be prompt and internally synchronized.
        Reads intentionally accept races with sync after this state snapshot;
        writes use the store's separate readiness and file locks.
        """
        binding = self._store.get_binding(agent_id, vault_id)
        sync_state = self._sync_state_for(vault_id)
        if sync_state == VaultSyncState.FAILED:
            raise VaultUnavailableError(f'Vault {vault_id!r} first sync failed')
        if sync_state == VaultSyncState.NOT_STARTED:
            raise SyncPendingError(f'Vault {vault_id!r} first sync has not started')
        if sync_state not in (VaultSyncState.SYNCING, VaultSyncState.PARTIAL, VaultSyncState.READY):
            raise VaultUnavailableError(f'Vault {vault_id!r} has unknown sync state {sync_state!r}')
        if require_ready:
            self._store.require_ready(agent_id, vault_id)
        return self._vault_root_for(vault_id), list(binding.roots)

    def list_dir(self, agent_id: str, vault_id: str, path: str) -> list[str]:
        """Return the sorted names (files and dirs) directly under path, non-recursive.

        `path` must resolve under one of the agent's configured roots — e.g.
        pass the root itself (`'Journal'`) to list a root folder; there is
        no way to list the vault root itself since it is never one of the
        configured roots.
        The checkout may change during traversal while initial or continuous
        sync is active; callers intentionally accept that partial-tree race.
        """
        vault_root, roots = self._binding_context(agent_id, vault_id, require_ready=False)
        return list_dir_under_roots(vault_root, roots=roots, rel_path=path)

    def read_text(self, agent_id: str, vault_id: str, path: str) -> str:
        """Return the UTF-8 text content of the file at path.

        Raises `FileNotFoundError` if path does not exist. Sync may concurrently
        replace content, so a torn read is intentionally accepted.
        """
        vault_root, roots = self._binding_context(agent_id, vault_id, require_ready=False)
        fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=os.O_RDONLY)
        with os.fdopen(fd, 'r', encoding='utf-8') as handle:
            return handle.read()

    def write_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite a ready-vault file while holding its writer lock."""
        vault_root, roots = self._binding_context(agent_id, vault_id, require_ready=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with self._store.lock_for(vault_id):
            fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=flags, create_parents=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(content)

    def append_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Append to a ready-vault file while holding its writer lock."""
        vault_root, roots = self._binding_context(agent_id, vault_id, require_ready=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        with self._store.lock_for(vault_id):
            fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=flags, create_parents=True)
            with os.fdopen(fd, 'a', encoding='utf-8') as handle:
                handle.write(content)
