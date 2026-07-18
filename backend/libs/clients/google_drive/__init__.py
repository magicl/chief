# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Public Google Drive metadata client foundation."""

from libs.clients.google_drive.client import GoogleDriveClient
from libs.clients.google_drive.config import (
    GoogleDriveConfig,
    GoogleDriveRoot,
    parse_google_drive_config,
)
from libs.clients.google_drive.errors import (
    GoogleDriveAPIError,
    GoogleDriveAuthError,
    GoogleDriveConfigError,
    GoogleDriveError,
    GoogleDriveForbiddenError,
    GoogleDriveInvalidCursorError,
    GoogleDriveNotFoundError,
    GoogleDriveOutsideRootError,
    GoogleDriveRateLimitedError,
)
from libs.clients.google_drive.protocol import GoogleDriveClientProtocol

__all__ = [
    'GoogleDriveAPIError',
    'GoogleDriveAuthError',
    'GoogleDriveClient',
    'GoogleDriveClientProtocol',
    'GoogleDriveConfig',
    'GoogleDriveConfigError',
    'GoogleDriveError',
    'GoogleDriveForbiddenError',
    'GoogleDriveInvalidCursorError',
    'GoogleDriveNotFoundError',
    'GoogleDriveOutsideRootError',
    'GoogleDriveRateLimitedError',
    'GoogleDriveRoot',
    'parse_google_drive_config',
]
