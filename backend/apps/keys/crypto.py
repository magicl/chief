# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Fernet encrypt/decrypt for credential payloads."""

from __future__ import annotations

import logging

from apps.keys.exceptions import KeyStorageMisconfiguredError
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def _master_key_bytes() -> bytes:
    """Return the configured Fernet key bytes from ``settings.CREDENTIALS_KEY``."""
    key = getattr(settings, 'CREDENTIALS_KEY', None) or ''
    if not key:
        raise ImproperlyConfigured('CREDENTIALS_KEY must be set')
    return key.encode('ascii')


def _fernet() -> Fernet:
    """Build a Fernet instance from the configured master key."""
    try:
        return Fernet(_master_key_bytes())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured('credential storage misconfigured') from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 secret for storage in ``encrypted_value``."""
    return _fernet().encrypt(plaintext.encode('utf-8'))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt stored ciphertext for immediate operational use (do not cache)."""
    try:
        return _fernet().decrypt(ciphertext).decode('utf-8')
    except InvalidToken:
        logger.warning('credential decrypt failed')
        raise KeyStorageMisconfiguredError('credential storage misconfigured') from None
