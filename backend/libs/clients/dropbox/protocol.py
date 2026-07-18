# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Structural interface for Dropbox metadata clients."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DropboxClientProtocol(Protocol):
    """Define the four root-safe, metadata-only operations shared by both clients."""

    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured Dropbox roots only."""

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one page of direct children beneath an authorized folder."""

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch one item after checking its current normalized path."""

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Run bounded native search and discard results outside the selected root."""
