# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Typed ClickUp client failures (mapped to tool/source failure results by callers)."""

from __future__ import annotations


class ClickUpError(Exception):
    """Base class for all ClickUp client failures."""


class ClickUpAuthError(ClickUpError):
    """Missing/invalid token (401/403)."""


class ClickUpNotFoundError(ClickUpError):
    """Referenced task/list/space does not exist (404)."""


class ClickUpAPIError(ClickUpError):
    """Other non-2xx ClickUp response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
