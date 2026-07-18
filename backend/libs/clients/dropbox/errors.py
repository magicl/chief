# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Typed failures exposed by the Dropbox metadata client."""

from __future__ import annotations


class DropboxError(Exception):
    """Base class for Dropbox metadata client failures."""


class DropboxAuthError(DropboxError):
    """Refresh credential parsing or authentication failed."""


class DropboxForbiddenError(DropboxError):
    """The Dropbox identity lacks permission for the requested item."""


class DropboxOutsideRootError(DropboxError):
    """The current normalized item path is outside the configured root."""


class DropboxNotFoundError(DropboxError):
    """The requested Dropbox item is not currently visible."""


class DropboxRateLimitedError(DropboxError):
    """The bounded provider retry was exhausted by a rate response."""


class DropboxInvalidCursorError(DropboxError):
    """The cursor is malformed or bound to a different invocation context."""


class DropboxConfigError(DropboxError):
    """Non-secret Dropbox integration configuration is invalid."""


class DropboxAPIError(DropboxError):
    """A remaining Dropbox SDK or transport operation failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Retain a safe status code without retaining provider response content."""
        super().__init__(message)
        self.status = status
