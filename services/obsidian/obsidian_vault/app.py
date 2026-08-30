# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""FastAPI application factory for the Obsidian vault HTTP API.

Wires bearer-token auth (`auth.BearerTokenAuth`), agent/vault lifecycle
routes (ensure/release/status), and path-gated file routes (list/read/write/
append) on top of already-constructed `VaultBindingStore`, `VaultFileService`,
and `HeadlessSupervisor` collaborators. All mapping from the lower layers'
typed exceptions to the shared `{"ok": false, "error": {...}}` response body
lives here — `bindings`/`files`/`paths`/`supervisor` only raise.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from obsidian_vault.auth import BearerTokenAuth
from obsidian_vault.bindings import SyncPendingError, VaultBindingStore
from obsidian_vault.files import VaultFileService, VaultUnavailableError
from obsidian_vault.paths import PathGateError
from obsidian_vault.supervisor import HeadlessSupervisor
from pydantic import BaseModel


class VaultCredential(BaseModel):
    """Obsidian Sync credential material for one vault binding."""

    auth_token: str
    encryption_password: str | None = None


class VaultBindingSpec(BaseModel):
    """One agent's desired binding to a vault: id, allowed roots, and Sync credential."""

    vault_id: str
    roots: list[str]
    credential: VaultCredential


class EnsureBindingsRequest(BaseModel):
    """Body of `PUT /v1/agents/{agent_id}/vaults` — the agent's full desired binding set."""

    bindings: list[VaultBindingSpec]


class FileContentBody(BaseModel):
    """Body of the write/append file routes: content as JSON rather than a raw text body."""

    content: str


def _error_body(kind: str, message: str) -> dict[str, Any]:
    """Build the shared `{"ok": false, "error": {"kind": ..., "message": ...}}` response body."""
    return {'ok': False, 'error': {'kind': kind, 'message': message}}


def _file_op_error_response(exc: Exception) -> JSONResponse:
    """Map a file-op exception to the shared error JSON response with the right HTTP status.

    Pending, unavailable, path-gate, and missing-path failures have explicit
    mappings; anything else (including `KeyError` for an unbound agent) is
    reported as `unavailable` per the vault service's normative contract.
    """
    if isinstance(exc, SyncPendingError):
        return JSONResponse(status_code=503, content=_error_body('sync_pending', str(exc)))
    if isinstance(exc, VaultUnavailableError):
        return JSONResponse(status_code=500, content=_error_body('unavailable', str(exc)))
    if isinstance(exc, PathGateError):
        return JSONResponse(status_code=403, content=_error_body('outside_root', str(exc)))
    if isinstance(exc, FileNotFoundError):
        return JSONResponse(status_code=404, content=_error_body('not_found', str(exc)))
    return JSONResponse(status_code=500, content=_error_body('unavailable', str(exc)))


def create_app(
    *,
    token: str,
    store: VaultBindingStore,
    files: VaultFileService,
    supervisor: HeadlessSupervisor,
    on_startup: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the vault service FastAPI app wired to the given collaborators.

    `store`/`files`/`supervisor` are injected so tests can wire a
    `FakeSupervisor` and an isolated `VaultBindingStore`/`VaultFileService`
    pair; `main.py` wires the real implementations from environment
    configuration. `files` is assumed to already be bound to `store`.
    Optional `on_startup` runs once during ASGI lifespan (e.g. Chief snapshot
    reconcile) before the server accepts traffic.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Run optional startup reconcile, then yield until shutdown."""
        if on_startup is not None:
            await asyncio.to_thread(on_startup)
        yield

    app = FastAPI(title='Obsidian Vault Service', lifespan=lifespan)
    auth = BearerTokenAuth(token)
    authenticated = Depends(auth)

    def _guarded_readiness(
        vault_id: str,
        *,
        observed_complete: bool | None = None,
    ) -> tuple[bool, bool]:
        """Publish and snapshot readiness only while a live reference still exists.

        A true marker observation made before locking is rechecked under the
        per-vault lock because last-reference teardown may clear it meanwhile.
        Callers must not hold this lock while waiting on supervisor startup.
        """
        with store.lock_for(vault_id):
            initial_sync_complete = (
                supervisor.is_initial_sync_complete(vault_id)
                if observed_complete is None or observed_complete
                else False
            )
            if store.has_references(vault_id) and initial_sync_complete:
                store.mark_vault_ready(vault_id)
            return initial_sync_complete, store.is_vault_ready(vault_id)

    def _start_supervisor_and_maybe_ready(vault_id: str, credential: VaultCredential) -> None:
        """Start (or reuse) supervisor sync for vault_id; mark it ready if already fully synced.

        The supervisor owns single-flight with short internal state locking,
        so its potentially long call runs without the store's file lock.
        Ready publication then uses that lock to exclude last-ref teardown.
        """
        supervisor.ensure_vault(
            vault_id,
            auth_token=credential.auth_token,
            encryption_password=credential.encryption_password,
        )
        _guarded_readiness(vault_id)

    @app.put('/v1/agents/{agent_id}/vaults', dependencies=[authenticated])
    def ensure_vaults(agent_id: str, body: EnsureBindingsRequest) -> dict[str, Any]:
        """Upsert agent_id's vault bindings, starting supervisor sync for any newly needed vault."""
        bindings_by_vault = {binding.vault_id: binding for binding in body.bindings}
        needs_start = store.ensure_agent(
            agent_id,
            [
                {
                    'vault_id': binding.vault_id,
                    'roots': binding.roots,
                    'credential': binding.credential.model_dump(),
                }
                for binding in body.bindings
            ],
        )
        for vault_id in needs_start:
            _start_supervisor_and_maybe_ready(vault_id, bindings_by_vault[vault_id].credential)

        vaults = [
            {'vault_id': vault_id, 'ready': store.get_binding(agent_id, vault_id).ready}
            for vault_id in bindings_by_vault
        ]
        return {'ok': True, 'vaults': vaults}

    @app.delete('/v1/agents/{agent_id}/vaults', dependencies=[authenticated])
    def release_vaults(agent_id: str) -> dict[str, Any]:
        """Release all of agent_id's bindings; stop supervisor sync for any vault that hit refcount 0."""
        released = store.release_agent(agent_id)
        for vault_id in released:
            with store.lock_for(vault_id):
                if not store.has_references(vault_id):
                    supervisor.stop_vault(vault_id)
        return {'ok': True, 'released': released}

    @app.get('/v1/vaults/{vault_id}/status', dependencies=[authenticated])
    def vault_status(vault_id: str) -> dict[str, Any]:
        """Report vault readiness, polling the supervisor to mark first-sync completion if newly done.

        Doubles as the poll mechanism for callers that ensured with a
        supervisor still mid-sync: each call re-checks
        `is_initial_sync_complete` and flips the store to ready the first
        time it observes completion.
        """
        observed_complete = supervisor.is_initial_sync_complete(vault_id)
        initial_sync_complete, ready = _guarded_readiness(vault_id, observed_complete=observed_complete)
        return {
            'vault_id': vault_id,
            'ready': ready,
            'initial_sync_complete': initial_sync_complete,
            'sync_process_alive': supervisor.is_process_alive(vault_id),
        }

    @app.get('/v1/agents/{agent_id}/files', dependencies=[authenticated])
    def list_files(agent_id: str, vault_id: str, path: str) -> Any:
        """List directory entries directly under path (must resolve within the agent's roots)."""
        try:
            entries = files.list_dir(agent_id, vault_id, path)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            return _file_op_error_response(exc)
        return {'ok': True, 'entries': entries}

    @app.get('/v1/agents/{agent_id}/files/content', dependencies=[authenticated])
    def read_file(agent_id: str, vault_id: str, path: str) -> Any:
        """Read the UTF-8 text content of the file at path."""
        try:
            content = files.read_text(agent_id, vault_id, path)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            return _file_op_error_response(exc)
        return {'ok': True, 'content': content}

    @app.put('/v1/agents/{agent_id}/files/content', dependencies=[authenticated])
    def write_file(agent_id: str, vault_id: str, path: str, body: FileContentBody) -> Any:
        """Create or overwrite the file at path with the JSON body's `content`."""
        try:
            files.write_text(agent_id, vault_id, path, body.content)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            return _file_op_error_response(exc)
        return {'ok': True}

    @app.post('/v1/agents/{agent_id}/files/append', dependencies=[authenticated])
    def append_file(agent_id: str, vault_id: str, path: str, body: FileContentBody) -> Any:
        """Append the JSON body's `content` to the file at path, creating it if missing."""
        try:
            files.append_text(agent_id, vault_id, path, body.content)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            return _file_op_error_response(exc)
        return {'ok': True}

    return app
