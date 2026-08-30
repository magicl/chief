# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Structural interface for Obsidian vault clients used by the `obsidian` tool."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ObsidianVaultClientProtocol(Protocol):
    """Define the lifecycle, status, and file operations dispatched by `ObsidianTool`."""

    def ensure_vaults(self, bindings: list[dict[str, Any]]) -> None:
        """Upsert this agent's desired vault bindings."""

    def release_vaults(self) -> None:
        """Release all of this agent's vault bindings."""

    def get_status(self, *, vault_id: str) -> dict[str, Any]:
        """Return first-sync readiness and continuous-sync process liveness for vault_id."""

    def list_dir(self, *, vault_id: str, path: str) -> list[str]:
        """List entry names directly under `path` within the agent's configured roots."""

    def read_text(self, *, vault_id: str, path: str) -> str:
        """Return the UTF-8 text content of the file at `path`."""

    def write_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Create or overwrite the file at `path` with `content`."""

    def append_text(self, *, vault_id: str, path: str, content: str) -> None:
        """Append `content` to the file at `path`, creating it if missing."""
