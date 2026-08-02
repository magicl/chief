# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Path-gated, readiness-gated filesystem file operations for vaults.

`VaultFileService` is the only place file IO happens for the vault service.
Every method follows the same sequence: check the vault's first-sync
readiness (`SyncPendingError` if not ready), take the per-vault lock, then
perform UTF-8 IO via O_NOFOLLOW descriptor walks (`PathGateError` if the
path escapes roots or hits a symlink). Callers map `SyncPendingError` /
`PathGateError` to retryable / hard-failure responses.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from obsidian_vault.bindings import VaultBindingStore
from obsidian_vault.paths import list_dir_under_roots, open_file_under_roots


class VaultFileService:
    """List/read/write/append operations for a single vault's working tree.

    `vault_root_for` maps a `vault_id` to the filesystem root of its
    checkout; this class does not know how that checkout was produced
    (headless Sync, tests, etc.) and never touches paths outside what
    the caller's binding roots allow.
    """

    def __init__(self, store: VaultBindingStore, vault_root_for: Callable[[str], Path]) -> None:
        """Store the binding store and vault-root resolver used by every file op."""
        self._store = store
        self._vault_root_for = vault_root_for

    def _binding_context(self, agent_id: str, vault_id: str) -> tuple[Path, list[str]]:
        """Gate readiness and return (vault_root, roots) for the agent binding."""
        self._store.require_ready(agent_id, vault_id)
        binding = self._store.get_binding(agent_id, vault_id)
        return self._vault_root_for(vault_id), list(binding.roots)

    def list_dir(self, agent_id: str, vault_id: str, path: str) -> list[str]:
        """Return the sorted names (files and dirs) directly under path, non-recursive.

        `path` must resolve under one of the agent's configured roots — e.g.
        pass the root itself (`'Journal'`) to list a root folder; there is
        no way to list the vault root itself since it is never one of the
        configured roots.
        """
        vault_root, roots = self._binding_context(agent_id, vault_id)
        with self._store.lock_for(vault_id):
            return list_dir_under_roots(vault_root, roots=roots, rel_path=path)

    def read_text(self, agent_id: str, vault_id: str, path: str) -> str:
        """Return the UTF-8 text content of the file at path.

        Raises `FileNotFoundError` if path does not exist.
        """
        vault_root, roots = self._binding_context(agent_id, vault_id)
        with self._store.lock_for(vault_id):
            fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=os.O_RDONLY)
            with os.fdopen(fd, 'r', encoding='utf-8') as handle:
                return handle.read()

    def write_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite the file at path with content, creating parent dirs as needed."""
        vault_root, roots = self._binding_context(agent_id, vault_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with self._store.lock_for(vault_id):
            fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=flags, create_parents=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(content)

    def append_text(self, agent_id: str, vault_id: str, path: str, content: str) -> None:
        """Append content to the file at path, creating the file and parent dirs if missing."""
        vault_root, roots = self._binding_context(agent_id, vault_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        with self._store.lock_for(vault_id):
            fd = open_file_under_roots(vault_root, roots=roots, rel_path=path, flags=flags, create_parents=True)
            with os.fdopen(fd, 'a', encoding='utf-8') as handle:
                handle.write(content)
