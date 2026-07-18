# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Typed failures exposed by the Google Drive metadata client."""

from __future__ import annotations


class GoogleDriveError(Exception):
    """Base class for Drive metadata client failures."""


class GoogleDriveAuthError(GoogleDriveError):
    """Credential parsing, authentication, or delegation failed."""


class GoogleDriveForbiddenError(GoogleDriveError):
    """The Google identity lacks permission for the requested item."""


class GoogleDriveOutsideRootError(GoogleDriveError):
    """The current item ancestry does not reach the configured root."""


class GoogleDriveNotFoundError(GoogleDriveError):
    """The requested Drive item is not currently visible."""


class GoogleDriveRateLimitedError(GoogleDriveError):
    """The bounded provider retry was exhausted by a quota response."""


class GoogleDriveInvalidCursorError(GoogleDriveError):
    """The cursor is malformed or bound to a different invocation context."""


class GoogleDriveConfigError(GoogleDriveError):
    """Non-secret Drive integration configuration is invalid."""


class GoogleDriveAPIError(GoogleDriveError):
    """A remaining Google Drive API operation failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Retain a safe status code without retaining provider response content."""
        super().__init__(message)
        self.status = status
