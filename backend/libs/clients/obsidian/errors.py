# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Typed failures exposed by the Obsidian vault HTTP client.

Mirrors the vault service's normative error body kinds (see
`services/obsidian/obsidian_vault/app.py`): `sync_pending`, `outside_root`,
`not_found`, `forbidden`, `auth`, `config`, `unavailable`. `sync_pending` and
`unavailable` are retryable by callers (e.g. the `obsidian` tool).
"""

from __future__ import annotations


class ObsidianVaultError(Exception):
    """Base class for all Obsidian vault client failures."""


class ObsidianSyncPendingError(ObsidianVaultError):
    """The vault has not completed its first Sync yet (kind ``sync_pending``, retryable)."""


class ObsidianOutsideRootError(ObsidianVaultError):
    """The requested path escapes or falls outside the binding's configured roots."""


class ObsidianNotFoundError(ObsidianVaultError):
    """The requested vault file or directory does not currently exist."""


class ObsidianAuthError(ObsidianVaultError):
    """Missing/invalid inter-service bearer token (kind ``auth`` or a bare 401)."""


class ObsidianForbiddenError(ObsidianVaultError):
    """The vault service denied the request for a reason other than root scoping."""


class ObsidianConfigError(ObsidianVaultError):
    """Non-secret Obsidian tool/client configuration is invalid."""


class ObsidianUnavailableError(ObsidianVaultError):
    """The vault service is unreachable or failed unexpectedly (kind ``unavailable``, retryable)."""
