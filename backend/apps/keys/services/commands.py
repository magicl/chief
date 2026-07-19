# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Credential write commands — never return plaintext."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from apps.bus.resources import publish_resource_update_after_commit
from apps.keys import crypto
from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import (
    CredentialAuthKind,
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
from django.db import transaction


def _validate_secret(secret: str, *, allow_empty: bool = False) -> str:
    """Normalize a secret and enforce storage limits plus caller emptiness policy."""
    normalized = secret.strip('\r\n\t ')
    if not normalized and not allow_empty:
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
    """Create or replace a database-owned static key and clear OAuth metadata."""
    validate_type(type_name)
    validated_name = _validate_named_name(name, user_id=user_id)
    validated_secret = _validate_secret(secret)
    with transaction.atomic():
        existing = UserCredential.objects.select_for_update().filter(user_id=user_id, name=validated_name).first()
        if existing is not None and existing.source == CredentialSource.DISK:
            raise KeyValidationError(f'disk-sourced credential is read-only: {validated_name}')
        row, _ = UserCredential.objects.update_or_create(
            user_id=user_id,
            name=validated_name,
            defaults={
                'type': type_name,
                'encrypted_value': crypto.encrypt(validated_secret),
                'auth_kind': CredentialAuthKind.STATIC,
                'auth_config': {},
                'source': CredentialSource.DB,
                'source_path': '',
                'source_rev': '',
                'status': CredentialStatus.ACTIVE,
            },
        )
        publish_resource_update_after_commit(user_id, 'keys')
    return _user_metadata(row)


def create_user_oauth(
    user_id: int,
    name: str,
    type_name: str,
    *,
    provider_id: str,
    capability_ids: Iterable[str],
) -> KeyMetadata:
    """Create or replace an unconnected database-owned OAuth declaration."""
    from apps.keys.oauth.services import normalize_auth_config

    validate_type(type_name)
    validated_name = _validate_named_name(name, user_id=user_id)
    auth_config = normalize_auth_config(
        provider_id=provider_id,
        credential_type=type_name,
        capability_ids=capability_ids,
    )
    with transaction.atomic():
        existing = UserCredential.objects.select_for_update().filter(user_id=user_id, name=validated_name).first()
        if existing is not None and existing.source == CredentialSource.DISK:
            raise KeyValidationError(f'disk-sourced credential is read-only: {validated_name}')
        row, _ = UserCredential.objects.update_or_create(
            user_id=user_id,
            name=validated_name,
            defaults={
                'type': type_name,
                'encrypted_value': b'',
                'auth_kind': CredentialAuthKind.OAUTH,
                'auth_config': auth_config,
                'source': CredentialSource.DB,
                'source_path': '',
                'source_rev': '',
                'status': CredentialStatus.ACTIVE,
            },
        )
        publish_resource_update_after_commit(user_id, 'keys')
    return _user_metadata(row)


def _is_structural_oauth_config(auth_config: Mapping[str, object]) -> bool:
    """Check the canonical OAuth JSON structure before provider code sees it."""
    if set(auth_config) != {'provider', 'capabilities'}:
        return False
    provider_id = auth_config.get('provider')
    capabilities = auth_config.get('capabilities')
    return (
        isinstance(provider_id, str)
        and bool(provider_id.strip())
        and isinstance(capabilities, list)
        and bool(capabilities)
        and all(isinstance(capability, str) and bool(capability.strip()) for capability in capabilities)
    )


def _normalize_disk_oauth_config(type_name: str, auth_config: Mapping[str, object]) -> dict[str, object]:
    """Validate and canonicalize disk OAuth metadata without retaining grants."""
    from apps.keys.oauth.services import normalize_auth_config

    if not _is_structural_oauth_config(auth_config):
        raise KeyValidationError('OAuth credential configuration is invalid')
    provider_id = auth_config['provider']
    capabilities = auth_config['capabilities']
    if not isinstance(provider_id, str) or not isinstance(capabilities, list):
        # The structural predicate above narrows values for humans, not static type checkers.
        raise KeyValidationError('OAuth credential configuration is invalid')
    return normalize_auth_config(
        provider_id=provider_id,
        credential_type=type_name,
        capability_ids=capabilities,
    )


def _existing_disk_auth_config(row: UserCredential) -> dict[str, object] | None:
    """Return canonical stored OAuth metadata, or none when it is malformed."""
    if row.auth_kind == CredentialAuthKind.STATIC:
        return {} if row.auth_config == {} else None
    if row.auth_kind != CredentialAuthKind.OAUTH or not isinstance(row.auth_config, dict):
        return None
    if not _is_structural_oauth_config(row.auth_config):
        return None
    try:
        return _normalize_disk_oauth_config(row.type, row.auth_config)
    except KeyValidationError:
        return None


def upsert_user_named_from_disk(
    user_id: int,
    name: str,
    type_name: str,
    secret: str | None,
    *,
    auth_kind: str = CredentialAuthKind.STATIC,
    auth_config: Mapping[str, object] | None = None,
    source_path: str,
    source_rev: str,
) -> tuple[KeyMetadata, bool]:
    """Reconcile one disk declaration while preserving only semantically valid grants."""
    validate_type(type_name)
    validated_name = _validate_named_name(name, user_id=user_id)
    supplied_auth_config = auth_config if auth_config is not None else {}
    if auth_kind == CredentialAuthKind.STATIC:
        if not isinstance(secret, str) or dict(supplied_auth_config):
            raise KeyValidationError('static credential configuration is invalid')
        normalized_auth_config: dict[str, object] = {}
        validated_secret = _validate_secret(secret, allow_empty=True)
    elif auth_kind == CredentialAuthKind.OAUTH:
        if secret is not None:
            raise KeyValidationError('OAuth credential configuration is invalid')
        normalized_auth_config = _normalize_disk_oauth_config(type_name, supplied_auth_config)
        validated_secret = None
    else:
        raise KeyValidationError('credential authentication kind is invalid')

    with transaction.atomic():
        row = (
            UserCredential.objects.select_for_update()
            .filter(
                user_id=user_id,
                name=validated_name,
            )
            .first()
        )
        if row is not None and row.source != CredentialSource.DISK:
            raise KeyValidationError(f'database-owned credential conflict: {validated_name}')
        semantic_match = (
            row is not None
            and row.type == type_name
            and row.auth_kind == auth_kind
            and _existing_disk_auth_config(row) == normalized_auth_config
        )
        if (
            row is not None
            and semantic_match
            and row.source_path == source_path
            and row.source_rev == source_rev
            and row.status == CredentialStatus.ACTIVE
        ):
            return _user_metadata(row), False

        encrypted_value = (
            bytes(row.encrypted_value)
            if auth_kind == CredentialAuthKind.OAUTH and semantic_match and row is not None
            else b'' if auth_kind == CredentialAuthKind.OAUTH else crypto.encrypt(validated_secret or '')
        )
        if row is None:
            row = UserCredential.objects.create(
                user_id=user_id,
                name=validated_name,
                type=type_name,
                encrypted_value=encrypted_value,
                auth_kind=auth_kind,
                auth_config=normalized_auth_config,
                source=CredentialSource.DISK,
                source_path=source_path,
                source_rev=source_rev,
                status=CredentialStatus.ACTIVE,
            )
        else:
            row.type = type_name
            row.encrypted_value = encrypted_value
            row.auth_kind = auth_kind
            row.auth_config = normalized_auth_config
            row.source_path = source_path
            row.source_rev = source_rev
            row.status = CredentialStatus.ACTIVE
            row.save(
                update_fields=[
                    'type',
                    'encrypted_value',
                    'auth_kind',
                    'auth_config',
                    'source_path',
                    'source_rev',
                    'status',
                    'updated_at',
                ]
            )
        publish_resource_update_after_commit(user_id, 'keys')
    return _user_metadata(row), True


@transaction.atomic
def delete_user_credential(user_id: int, name: str) -> None:
    """Delete a user key and notify after commit, preserving KeyNotFoundError."""
    deleted, _ = UserCredential.objects.filter(user_id=user_id, name=name).delete()
    if not deleted:
        raise KeyNotFoundError(f'credential not found: {name}')
    publish_resource_update_after_commit(user_id, 'keys')


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
