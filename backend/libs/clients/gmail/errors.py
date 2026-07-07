# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Typed Gmail client failures (mapped to tool/source failure results by callers)."""

from __future__ import annotations


class GmailError(Exception):
    """Base class for all Gmail client failures."""


class GmailAuthError(GmailError):
    """Service-account parse, impersonation, or scope authorization failure."""


class GmailNotFoundError(GmailError):
    """Referenced message or label does not exist."""


class GmailAPIError(GmailError):
    """Non-2xx Gmail API response other than auth/not-found."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
