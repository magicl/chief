# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shared mutation-aware results for local file synchronization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

SyncProgressCallback = Callable[[], None]


@dataclass(frozen=True)
class SyncItemResult:
    """Describe one file outcome and whether it changed a user's list."""

    source_path: str
    success: bool
    detail: str = ''
    user_id: int | None = None
    changed: bool = False


@dataclass
class SyncReport:
    """Collect file outcomes, disables, and users with visible mutations."""

    items: list[SyncItemResult] = field(default_factory=list)
    disabled: int = 0
    disabled_user_ids: set[int] = field(default_factory=set)

    @property
    def succeeded(self) -> int:
        """Return the number of successfully synchronized files."""
        return sum(item.success for item in self.items)

    @property
    def failed(self) -> int:
        """Return the number of files that could not be synchronized."""
        return sum(not item.success for item in self.items)

    @property
    def changed_user_ids(self) -> set[int]:
        """Return all owners whose list-visible state changed."""
        return {
            item.user_id for item in self.items if item.success and item.changed and item.user_id is not None
        } | self.disabled_user_ids
