# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Chief HTTP client for the Obsidian vault service."""

from libs.clients.obsidian.client import ObsidianVaultClient
from libs.clients.obsidian.config import ObsidianToolConfig, parse_obsidian_tool_config
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
from libs.clients.obsidian.protocol import ObsidianVaultClientProtocol

__all__ = [
    'ObsidianAuthError',
    'ObsidianConfigError',
    'ObsidianForbiddenError',
    'ObsidianNotFoundError',
    'ObsidianOutsideRootError',
    'ObsidianSyncPendingError',
    'ObsidianToolConfig',
    'ObsidianUnavailableError',
    'ObsidianVaultClient',
    'ObsidianVaultClientProtocol',
    'ObsidianVaultError',
    'parse_obsidian_tool_config',
]
