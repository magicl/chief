# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Gmail API client package."""

from libs.clients.gmail.client import GmailClient
from libs.clients.gmail.errors import (
    GmailAPIError,
    GmailAuthError,
    GmailError,
    GmailNotFoundError,
)

__all__ = [
    'GmailAPIError',
    'GmailAuthError',
    'GmailClient',
    'GmailError',
    'GmailNotFoundError',
]
