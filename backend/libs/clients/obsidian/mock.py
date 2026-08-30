# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-memory Obsidian vault client for tool and lifecycle tests.

Models the vault service's own first-sync gate and root scoping (see
`services/obsidian/obsidian_vault/{bindings,files,paths}.py`) closely enough
for `obsidian` tool tests to exercise partial reads during first sync,
`sync_pending` writes, and root enforcement without a running vault service.

As in the vault service, only mutations wait for first sync: list and read
serve whatever has landed so far, while write and append raise
`ObsidianSyncPendingError` until the vault is ready.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from libs.clients.obsidian.errors import (
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
    ObsidianUnavailableError,
)


@dataclass
class _VaultRecord:
    """In-memory state for one seeded vault: lifecycle flags and flat file content."""

    ready: bool = False
    failed: bool = False
    files: dict[str, str] = field(default_factory=dict)


def _segments(path: str) -> tuple[str, ...]:
    """Split a vault-relative path into non-empty posix segments."""
    return tuple(part for part in path.split('/') if part)


def _matches_configured_root(parts: tuple[str, ...], roots: list[str]) -> bool:
    """Return True if parts starts with one of roots as a full path segment.

    Mirrors `obsidian_vault.paths._matches_configured_root`: a root of
    `'Journal'` matches `'Journal/x'` but not `'Journalism/x'`.
    """
    for root in roots:
        root_parts = _segments(root)
        if root_parts and parts[: len(root_parts)] == root_parts:
            return True
    return False


class MockObsidianVaultClient:
    """Small in-memory `ObsidianVaultClient` replacement with deterministic mutation records."""

    def __init__(self, *, base_url: str = '', token: str = '', agent_id: str) -> None:
        """Create a mock scoped to one agent id, with the same constructor shape as the real client."""
        self._agent_id = agent_id
        self._vaults: dict[str, _VaultRecord] = {}
        self._bound_roots: dict[str, list[str]] = {}
        self._sync_alive: dict[str, bool] = {}
        self.ensure_calls: list[list[dict[str, Any]]] = []
        self.released = False

    def seed_vault(self, vault_id: str, *, ready: bool = True) -> None:
        """Add or replace a vault record, defaulting to first-sync-complete."""
        self._vaults[vault_id] = _VaultRecord(ready=ready)

    def set_ready(self, vault_id: str, ready: bool) -> None:
        """Flip a seeded vault's first-sync readiness, modeling a late-completing sync."""
        record = self._vaults.setdefault(vault_id, _VaultRecord())
        record.ready = ready

    def set_failed(self, vault_id: str, failed: bool = True) -> None:
        """Set or clear a seeded vault's hard first-sync failure state.

        The vault must already be seeded so this test control cannot change
        unknown-vault operations from `not_found` to `unavailable`.
        """
        record = self._bound_record(vault_id)
        record.failed = failed
        if failed:
            record.ready = False

    def set_sync_process_alive(self, vault_id: str, alive: bool) -> None:
        """Override whether get_status reports the continuous sync child as running."""
        self._sync_alive[vault_id] = alive

    def get_status(self, *, vault_id: str) -> dict[str, Any]:
        """Return first-sync flags without the file-op ready/root gate.

        Unseeded vaults match the vault service's all-false 200 body. Seeded
        vaults use record.ready for both ready and initial_sync_complete;
        sync_process_alive defaults to True unless set_sync_process_alive.
        """
        record = self._vaults.get(vault_id)
        if record is None:
            return {
                'vault_id': vault_id,
                'ready': False,
                'initial_sync_complete': False,
                'sync_process_alive': False,
            }
        return {
            'vault_id': vault_id,
            'ready': record.ready,
            'initial_sync_complete': record.ready,
            'sync_process_alive': self._sync_alive.get(vault_id, not record.failed),
        }

    def seed_file(self, vault_id: str, path: str, content: str) -> None:
        """Seed one file's content directly, bypassing readiness/root checks."""
        record = self._vaults.setdefault(vault_id, _VaultRecord(ready=True))
        record.files[path] = content

    def ensure_vaults(self, bindings: list[dict[str, Any]]) -> None:
        """Record the ensure call and bind this agent's roots for each vault_id."""
        self.ensure_calls.append(deepcopy(bindings))
        for binding in bindings:
            vault_id = binding['vault_id']
            self._vaults.setdefault(vault_id, _VaultRecord())
            self._bound_roots[vault_id] = list(binding.get('roots', []))

    def release_vaults(self) -> None:
        """Record release and drop this agent's root bindings for every vault."""
        self.released = True
        self._bound_roots = {}

    def _bound_record(self, vault_id: str) -> _VaultRecord:
        """Resolve the seeded vault record, mirroring the service's binding lookup."""
        record = self._vaults.get(vault_id)
        if record is None:
            raise ObsidianNotFoundError(f'obsidian vault not seeded: {vault_id}')
        return record

    def _require_root(self, vault_id: str, path: str) -> None:
        """Raise unless path sits inside the roots this agent bound for vault_id."""
        roots = self._bound_roots.get(vault_id, [])
        if not _matches_configured_root(_segments(path), roots):
            raise ObsidianOutsideRootError(f'path is outside configured roots: {path!r}')

    def _require_available(self, vault_id: str, record: _VaultRecord) -> None:
        """Raise unavailable when the seeded vault is in hard first-sync failure."""
        if record.failed:
            raise ObsidianUnavailableError(f'obsidian vault first sync failed: {vault_id}')

    def _authorize_read(self, vault_id: str, path: str) -> _VaultRecord:
        """Resolve the binding then enforce root scope; reads never wait for first sync."""
        record = self._bound_record(vault_id)
        self._require_available(vault_id, record)
        self._require_root(vault_id, path)
        return record

    def _gate_write(self, vault_id: str, path: str) -> _VaultRecord:
        """Resolve the binding, require first-sync readiness, then enforce root scope.

        The order matters and matches `VaultFileService._binding_context`, which looks up the
        binding and calls `require_ready` before `open_file_under_roots` applies root
        scoping. So a vault that is still syncing reports `sync_pending` even for a path
        the agent may not touch, and only a ready vault reports `outside_root`.
        """
        record = self._bound_record(vault_id)
        self._require_available(vault_id, record)
        if not record.ready:
            raise ObsidianSyncPendingError(f'vault {vault_id!r} has not completed first sync yet')
        self._require_root(vault_id, path)
        return record

    def list_dir(self, *, vault_id: str, path: str) -> list[str]:
        """List direct child names (files and synthesized dirs) under path, ready or not."""
        record = self._authorize_read(vault_id, path)
        prefix = path.rstrip('/') + '/'
        names = {
            file_path[len(prefix) :].split('/', 1)[0] for file_path in record.files if file_path.startswith(prefix)
        }
        return sorted(names)

    def read_text(self, *, vault_id: str, path: str) -> str:
        """Return seeded content for path (ready or not), raising if it was never written."""
        record = self._authorize_read(vault_id, path)
        if path not in record.files:
            raise ObsidianNotFoundError(f'obsidian file not found: {path}')
        return record.files[path]

    def write_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite the seeded content at path once first sync has completed."""
        record = self._gate_write(vault_id, path)
        record.files[path] = content

    def append_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Append to (or create) the seeded content at path once first sync has completed."""
        record = self._gate_write(vault_id, path)
        record.files[path] = record.files.get(path, '') + content
