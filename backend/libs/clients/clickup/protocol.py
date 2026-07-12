# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Protocol for ClickUp clients used by the ClickUp tool."""

from __future__ import annotations

from typing import Any, Protocol


class ClickUpClientProtocol(Protocol):
    """Structural interface for the ClickUp methods dispatched by ClickUpTool."""

    def list_spaces(self, team_id: str) -> dict[str, Any]:
        """List spaces in one ClickUp workspace."""

    def list_lists(self, space_id: str) -> dict[str, Any]:
        """List folderless lists in one space."""

    def list_tasks(self, *, list_id: str, statuses: tuple[str, ...] = ()) -> dict[str, Any]:
        """List tasks in one list, optionally filtering by status names."""

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch one task by id."""

    def create_task(
        self, *, list_id: str, name: str, description: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """Create one task in a list."""

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Update arbitrary task fields."""

    def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
        """Create one comment on a task."""

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Delete one task."""
