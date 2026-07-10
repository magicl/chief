# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Credential write commands — never return plaintext."""

from __future__ import annotations

import uuid

from apps.keys import crypto
from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import (
    CredentialSource,
    CredentialStatus,
    SystemCredential,
    UserCredential,
)
from apps.keys.services.queries import KeyMetadata, _system_metadata, _user_metadata
from apps.keys.types import (
    MAX_SECRET_BYTES,
    RESERVED_USER_PREFIXES,
    USER_NAMED_NAME_RE,
    canonical_default_name,
    validate_type,
)


def _validate_secret(secret: str) -> str:
    """Strip outer whitespace and validate secret length for storage."""
    normalized = secret.strip('\r\n\t ')
    if not normalized:
        raise KeyValidationError('secret must not be empty')
    if len(normalized.encode('utf-8')) > MAX_SECRET_BYTES:
        raise KeyValidationError('secret exceeds maximum length')
    return normalized


def _validate_named_name(name: str, *, user_id: int | None = None) -> str:
    """Validate a user-chosen credential name and reserved-namespace rules."""
    stripped = name.strip()
    if not USER_NAMED_NAME_RE.match(stripped):
        raise KeyValidationError(f'invalid credential name: {name}')
    for prefix in RESERVED_USER_PREFIXES:
        if stripped.startswith(prefix):
            raise KeyValidationError(f'reserved credential name prefix: {prefix}')
    if SystemCredential.objects.filter(name=stripped).exists():
        raise KeyValidationError(f'name reserved by system credential: {stripped}')
    return stripped


def _unset_system_default_flag(type_name: str, *, except_pk: uuid.UUID | None = None) -> None:
    """Ensure at most one system credential per type is marked default."""
    qs = SystemCredential.objects.filter(type=type_name, is_default=True)
    if except_pk is not None:
        qs = qs.exclude(pk=except_pk)
    qs.update(is_default=False)


def upsert_user_named(user_id: int, name: str, type_name: str, secret: str) -> KeyMetadata:
    """Create or replace a database-owned active user credential and return metadata."""
    validate_type(type_name)
    validated_name = _validate_named_name(name, user_id=user_id)
    validated_secret = _validate_secret(secret)
    row, _ = UserCredential.objects.update_or_create(
        user_id=user_id,
        name=validated_name,
        defaults={
            'type': type_name,
            'encrypted_value': crypto.encrypt(validated_secret),
            'source': CredentialSource.DB,
            'source_path': '',
            'source_rev': '',
            'status': CredentialStatus.ACTIVE,
        },
    )
    return _user_metadata(row)


def delete_user_credential(user_id: int, name: str) -> None:
    """Delete a named user credential. Raises KeyNotFoundError if missing."""
    deleted, _ = UserCredential.objects.filter(user_id=user_id, name=name).delete()
    if not deleted:
        raise KeyNotFoundError(f'credential not found: {name}')


def set_system_default(type_name: str, secret: str) -> KeyMetadata:
    """Set or clear the system default credential for ``type_name`` (staff/admin).

    Creates or updates the named row marked ``is_default`` for the type.
    Empty secret clears the default row.
    """
    validate_type(type_name)
    name = canonical_default_name(type_name)
    stripped = secret.strip()
    if not stripped:
        deleted, _ = SystemCredential.objects.filter(type=type_name, is_default=True).delete()
        if not deleted:
            return KeyMetadata(
                name=name,
                scope='system',
                type=type_name,
                is_default=True,
                is_set=False,
                updated_at=None,
            )
        return KeyMetadata(
            name=name,
            scope='system',
            type=type_name,
            is_default=True,
            is_set=False,
            updated_at=None,
        )
    validated = _validate_secret(stripped)
    row, _ = SystemCredential.objects.update_or_create(
        type=type_name,
        is_default=True,
        defaults={
            'name': name,
            'encrypted_value': crypto.encrypt(validated),
        },
    )
    _unset_system_default_flag(type_name, except_pk=row.pk)
    return _system_metadata(row)


def delete_system_credential(name: str) -> None:
    """Delete a system credential by name. Raises KeyNotFoundError if missing."""
    deleted, _ = SystemCredential.objects.filter(name=name).delete()
    if not deleted:
        raise KeyNotFoundError(f'credential not found: {name}')
