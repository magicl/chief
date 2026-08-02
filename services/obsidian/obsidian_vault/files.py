# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Path-gated, readiness-gated filesystem file operations for vaults.

`VaultFileService` is the only place file IO happens for the vault service.
Every method follows the same sequence: check the vault's first-sync
readiness (`SyncPendingError` if not ready), resolve the caller-supplied
relative path under the agent's configured roots (`PathGateError` if it
escapes or falls outside those roots), take the per-vault lock, then do the
actual UTF-8 IO. Callers (e.g. the HTTP layer) are expected to map
`SyncPendingError` / `PathGateError` to retryable / hard-failure responses.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from obsidian_vault.bindings import VaultBindingStore
from obsidian_vault.paths import resolve_under_roots


class VaultFileService:
    """List/read/write/append operations for a single vault's working tree.

    `vault_root_for` maps a `vault_id` to the filesystem root of its
    checkout; this class does not know how that checkout was produced
    (headless Sync, tests, etc.) and never touches paths outside what
    `resolve_under_roots` allows for the calling agent's binding.
    """

    def __init__(self, store: VaultBindingStore, vault_root_for: Callable[[str], Path]) -> None:
        """Store the binding store and vault-root resolver used by every file op."""
        self._store = store
        self._vault_root_for = vault_root_for

    def _resolve(self, agent_id: str, vault_id: str, path: str) -> Path:
        """Gate readiness and roots for (agent_id, vault_id, path); return the resolved path.

        Raises `SyncPendingError` if the vault hasn't completed first sync,
        `KeyError` if agent_id has no binding for vault_id, and
        `PathGateError` if path escapes or falls outside the agent's roots.
        """
        self._store.require_ready(agent_id, vault_id)
        binding = self._store.get_binding(agent_id, vault_id)
        vault_root = self._vault_root_for(vault_id)
        return resolve_under_roots(vault_root, roots=binding.roots, rel_path=path)

    def list_dir(self, agent_id: str, vault_id: str, path: str) -> list[str]:
        """Return the sorted names (files and dirs) directly under path, non-recursive.

        `path` must resolve under one of the agent's configured roots — e.g.
        pass the root itself (`'Journal'`) to list a root folder; there is
        no way to list the vault root itself since it is never one of the
        configured roots.
        """
        resolved = self._resolve(agent_id, vault_id, path)
        with self._store.lock_for(vault_id):
            return sorted(entry.name for entry in resolved.iterdir())

    def read_text(self, agent_id: str, vault_id: str, path: str) -> str:
        """Return the UTF-8 text content of the file at path.

        Raises `FileNotFoundError` if path does not exist.
        """
        resolved = self._resolve(agent_id, vault_id, path)
        with self._store.lock_for(vault_id):
            return resolved.read_text(encoding='utf-8')

    def write_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite the file at path with content, creating parent dirs as needed."""
        resolved = self._resolve(agent_id, vault_id, path)
        with self._store.lock_for(vault_id):
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding='utf-8')

    def append_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Append content to the file at path, creating the file and parent dirs if missing."""
        resolved = self._resolve(agent_id, vault_id, path)
        with self._store.lock_for(vault_id):
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with resolved.open('a', encoding='utf-8') as handle:
                handle.write(content)
