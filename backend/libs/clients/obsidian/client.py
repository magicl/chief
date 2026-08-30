# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django-free HTTP client for the Obsidian vault service.

No official SDK exists for the (Chief-owned) vault service, so this is a thin
`httpx` wrapper mirroring `libs.clients.clickup.client.ClickUpClient`. The
inter-service bearer token is a static Compose-injected secret (see
`chief.settings.OBSIDIAN_VAULT_TOKEN`) rather than a per-user `apps.keys`
credential, so it is taken directly rather than through a lazy supplier.
`transport` is a test seam for `httpx.MockTransport`.

This client does not retry `sync_pending` / `unavailable` failures itself —
that stall/backoff policy belongs to the `obsidian` tool (Task 8), which
knows the session/tool timeout budget the client does not.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
from libs.clients.obsidian.errors import (
    ObsidianAuthError,
    ObsidianConfigError,
    ObsidianForbiddenError,
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
    ObsidianUnavailableError,
    ObsidianVaultError,
)

_TIMEOUT = 30.0

# Maps the vault service's normative `error.kind` values (see
# `services/obsidian/obsidian_vault/app.py` and the Task 7 plan's normative
# contract) to typed client failures.
_KIND_TO_ERROR: dict[str, type[ObsidianVaultError]] = {
    'sync_pending': ObsidianSyncPendingError,
    'outside_root': ObsidianOutsideRootError,
    'not_found': ObsidianNotFoundError,
    'forbidden': ObsidianForbiddenError,
    'auth': ObsidianAuthError,
    'config': ObsidianConfigError,
    'unavailable': ObsidianUnavailableError,
}


def _require_nonempty_str(value: str, *, field: str) -> str:
    """Require a non-empty string constructor argument."""
    if not isinstance(value, str) or not value.strip():
        raise ObsidianConfigError(f'{field} must be a non-empty string')
    return value


def _parse_status_body(body: dict[str, Any]) -> dict[str, Any]:
    """Require the vault-service status shape; reject truthy non-bools."""
    vault_id = body.get('vault_id')
    ready = body.get('ready')
    initial_sync_complete = body.get('initial_sync_complete')
    sync_process_alive = body.get('sync_process_alive')
    if (
        not isinstance(vault_id, str)
        or not vault_id
        or not isinstance(ready, bool)
        or not isinstance(initial_sync_complete, bool)
        or not isinstance(sync_process_alive, bool)
    ):
        raise ObsidianUnavailableError('obsidian vault service returned invalid status')
    return {
        'vault_id': vault_id,
        'ready': ready,
        'initial_sync_complete': initial_sync_complete,
        'sync_process_alive': sync_process_alive,
    }


class ObsidianVaultClient:
    """Thin wrapper over the Obsidian vault service's `/v1` HTTP API for one agent."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        agent_id: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the client to one agent id; every request is scoped to that agent."""
        self._base_url = _require_nonempty_str(base_url, field='base_url')
        self._token = _require_nonempty_str(token, field='token')
        self._agent_id = _require_nonempty_str(agent_id, field='agent_id')
        self._transport = transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue one request and map any non-2xx or transport failure to a typed error."""
        headers = {'Authorization': f'Bearer {self._token}'}
        try:
            with httpx.Client(base_url=self._base_url, transport=self._transport, timeout=_TIMEOUT) as client:
                resp = client.request(method, path, params=params, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise ObsidianUnavailableError(f'obsidian vault service unreachable ({path})') from exc
        return self._handle_response(resp, path=path)

    def _handle_response(self, resp: httpx.Response, *, path: str) -> dict[str, Any]:
        """Return the parsed JSON body on success; raise the typed failure it maps to otherwise."""
        if resp.status_code == 401:
            # The bearer-auth dependency raises a bare FastAPI HTTPException before any route
            # body executes, so unauthorized responses never carry the `{"error": {...}}` shape.
            raise ObsidianAuthError(f'obsidian vault service rejected inter-service auth ({path})')
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code < 400:
            if not isinstance(body, dict):
                raise ObsidianUnavailableError(f'obsidian vault service returned an invalid response ({path})')
            return cast(dict[str, Any], body)
        kind: str | None = None
        message = f'obsidian vault service failure ({resp.status_code}) at {path}'
        if isinstance(body, dict):
            error = body.get('error')
            if isinstance(error, dict):
                raw_kind = error.get('kind')
                if isinstance(raw_kind, str):
                    kind = raw_kind
                raw_message = error.get('message')
                if isinstance(raw_message, str) and raw_message:
                    message = raw_message
        error_cls = _KIND_TO_ERROR.get(kind, ObsidianUnavailableError) if kind is not None else ObsidianUnavailableError
        raise error_cls(message)

    def get_status(self, *, vault_id: str) -> dict[str, Any]:
        """Fetch vault-level first-sync and process-liveness flags (`GET /v1/vaults/{vault_id}/status`)."""
        return _parse_status_body(self._request('GET', f'/v1/vaults/{vault_id}/status'))

    def ensure_vaults(self, bindings: list[dict[str, Any]]) -> None:
        """Upsert this agent's desired vault bindings (`PUT /v1/agents/{agent_id}/vaults`).

        `bindings` mirrors the vault service's `EnsureBindingsRequest` shape: each entry
        needs `vault_id`, `roots`, and `credential` (Obsidian Sync `auth_token` /
        `encryption_password`). Callers resolve the Sync credential from `apps.keys` before
        calling this — the client never touches that resolution.
        """
        self._request('PUT', f'/v1/agents/{self._agent_id}/vaults', json_body={'bindings': bindings})

    def release_vaults(self) -> None:
        """Release all of this agent's vault bindings (`DELETE /v1/agents/{agent_id}/vaults`)."""
        self._request('DELETE', f'/v1/agents/{self._agent_id}/vaults')

    def list_dir(self, *, vault_id: str, path: str) -> list[str]:
        """List entry names directly under `path` (must resolve within the agent's roots)."""
        body = self._request(
            'GET',
            f'/v1/agents/{self._agent_id}/files',
            params={'vault_id': vault_id, 'path': path},
        )
        entries = body.get('entries')
        if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
            raise ObsidianUnavailableError('obsidian vault service returned invalid directory entries')
        return entries

    def read_text(self, *, vault_id: str, path: str) -> str:
        """Return the UTF-8 text content of the file at `path`."""
        body = self._request(
            'GET',
            f'/v1/agents/{self._agent_id}/files/content',
            params={'vault_id': vault_id, 'path': path},
        )
        content = body.get('content')
        if not isinstance(content, str):
            raise ObsidianUnavailableError('obsidian vault service returned invalid file content')
        return content

    def write_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite the file at `path` with `content`."""
        self._request(
            'PUT',
            f'/v1/agents/{self._agent_id}/files/content',
            params={'vault_id': vault_id, 'path': path},
            json_body={'content': content},
        )

    def append_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Append `content` to the file at `path`, creating it (and parents) if missing."""
        self._request(
            'POST',
            f'/v1/agents/{self._agent_id}/files/append',
            params={'vault_id': vault_id, 'path': path},
            json_body={'content': content},
        )
