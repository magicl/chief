# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Public Dropbox metadata client foundation."""

from libs.clients.dropbox.client import DropboxClient
from libs.clients.dropbox.config import (
    DropboxConfig,
    DropboxRoot,
    is_path_within,
    normalize_dropbox_path,
    parse_dropbox_config,
)
from libs.clients.dropbox.errors import (
    DropboxAPIError,
    DropboxAuthError,
    DropboxConfigError,
    DropboxError,
    DropboxForbiddenError,
    DropboxInvalidCursorError,
    DropboxNotFoundError,
    DropboxOutsideRootError,
    DropboxRateLimitedError,
)
from libs.clients.dropbox.protocol import DropboxClientProtocol

__all__ = [
    'DropboxAPIError',
    'DropboxAuthError',
    'DropboxClient',
    'DropboxClientProtocol',
    'DropboxConfig',
    'DropboxConfigError',
    'DropboxError',
    'DropboxForbiddenError',
    'DropboxInvalidCursorError',
    'DropboxNotFoundError',
    'DropboxOutsideRootError',
    'DropboxRateLimitedError',
    'DropboxRoot',
    'is_path_within',
    'normalize_dropbox_path',
    'parse_dropbox_config',
]
