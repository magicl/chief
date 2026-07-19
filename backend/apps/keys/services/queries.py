# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Credential metadata and resolve queries."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from apps.keys import crypto
from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyTypeMismatchError,
    KeyValidationError,
)
from apps.keys.models import (
    CredentialAuthKind,
    CredentialStatus,
    SystemCredential,
    UserCredential,
)
from apps.keys.oauth import OAUTH_PROVIDERS
from apps.keys.types import LLM_ENV_FALLBACK, validate_type


@dataclass(frozen=True)
class KeyMetadata:
    """Credential slot metadata for UI and pickers — never includes plaintext."""

    name: str
    scope: Literal['system', 'user']
    type: str
    is_default: bool
    is_set: bool
    updated_at: datetime | None
    id: uuid.UUID | None = None
    source: str = 'db'
    status: str = 'active'
    auth_kind: str = 'static'
    oauth_provider: str | None = None
    oauth_capabilities: tuple[str, ...] = ()


def _is_set(encrypted_value: bytes | memoryview) -> bool:
    return bool(encrypted_value)


def _as_bytes(encrypted_value: bytes | memoryview) -> bytes:
    if isinstance(encrypted_value, memoryview):
        return encrypted_value.tobytes()
    return encrypted_value


def _system_metadata(row: SystemCredential) -> KeyMetadata:
    return KeyMetadata(
        name=row.name,
        scope='system',
        type=row.type,
        is_default=row.is_default,
        is_set=_is_set(row.encrypted_value),
        updated_at=row.updated_at,
    )


def get_oauth_metadata(row: UserCredential) -> tuple[str | None, tuple[str, ...]]:
    """Return only registry-validated, canonical OAuth metadata without decrypting."""
    if row.auth_kind != CredentialAuthKind.OAUTH or not isinstance(row.auth_config, dict):
        return None, ()
    if set(row.auth_config) != {'provider', 'capabilities'}:
        return None, ()

    provider = row.auth_config.get('provider')
    capabilities = row.auth_config.get('capabilities')
    if not isinstance(provider, str) or not provider or provider != provider.strip():
        return None, ()
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or any(
            not isinstance(capability, str) or not capability or capability != capability.strip()
            for capability in capabilities
        )
    ):
        return None, ()
    try:
        registered_provider = OAUTH_PROVIDERS.get(provider)
        if registered_provider.credential_type != row.type:
            return None, ()
        normalized = registered_provider.normalize_capabilities(capabilities)
    except KeyValidationError:
        return None, ()
    if capabilities != list(normalized):
        return None, ()
    return registered_provider.id, normalized


def _user_metadata(row: UserCredential) -> KeyMetadata:
    """Build UI-safe metadata including user credential provenance and status."""
    oauth_provider, oauth_capabilities = get_oauth_metadata(row)
    return KeyMetadata(
        name=row.name,
        scope='user',
        type=row.type,
        is_default=False,
        is_set=_is_set(row.encrypted_value),
        updated_at=row.updated_at,
        id=row.pk,
        source=row.source,
        status=row.status,
        auth_kind=row.auth_kind,
        oauth_provider=oauth_provider,
        oauth_capabilities=oauth_capabilities,
    )


def list_system_credentials() -> list[KeyMetadata]:
    """List all system-scoped credential slots (metadata only)."""
    return [_system_metadata(row) for row in SystemCredential.objects.order_by('name')]


def list_user_credentials(user_id: int) -> list[KeyMetadata]:
    """List credential slots owned by ``user_id`` (metadata only)."""
    return [_user_metadata(row) for row in UserCredential.objects.filter(user_id=user_id).order_by('name')]


def get_owned_user_credential(user_id: int, credential_id: uuid.UUID) -> UserCredential:
    """Return one user-owned credential row without decrypting its value."""
    try:
        return UserCredential.objects.get(user_id=user_id, pk=credential_id)
    except UserCredential.DoesNotExist:
        raise KeyNotFoundError('credential not found') from None


def list_referenceable_credentials(
    user_id: int,
    *,
    type: str | None = None,
) -> list[KeyMetadata]:
    """System + user credentials the given user may reference.

    Optional filter by service type (for agent/tool picker UI).
    """
    system_qs = SystemCredential.objects.all()
    user_qs = UserCredential.objects.filter(user_id=user_id)
    if type is not None:
        validate_type(type)
        system_qs = system_qs.filter(type=type)
        user_qs = user_qs.filter(type=type)
    system_rows = [_system_metadata(row) for row in system_qs.order_by('name')]
    user_rows = [_user_metadata(row) for row in user_qs.order_by('name')]
    return system_rows + user_rows


def _decrypt_row(encrypted_value: bytes | memoryview) -> str:
    return crypto.decrypt(_as_bytes(encrypted_value))


def _env_fallback(type_name: str) -> str | None:
    env_var = LLM_ENV_FALLBACK.get(type_name)
    if env_var is None:
        return None
    return os.environ.get(env_var) or None


def resolve_default_secret(user_id: int, type_name: str) -> str | None:
    """Resolution order: system default → env fallback (LLM only).

    ``user_id`` is retained for call-site compatibility; user-level defaults are not used.
    Returns decrypted secret or None.
    """
    del user_id
    validate_type(type_name)
    system_row = SystemCredential.objects.filter(
        type=type_name,
        is_default=True,
    ).first()
    if system_row is not None and _is_set(system_row.encrypted_value):
        return _decrypt_row(system_row.encrypted_value)
    return _env_fallback(type_name)


def resolve_secret(user_id: int, name: str, *, expected_type: str) -> str:
    """Resolve by name, skipping disabled user rows before the system fallback.

    Validates type match and raises KeyNotFoundError or KeyTypeMismatchError.
    """
    validate_type(expected_type)
    user_row = UserCredential.objects.filter(
        user_id=user_id,
        name=name,
        status=CredentialStatus.ACTIVE,
    ).first()
    if user_row is not None:
        if user_row.type != expected_type:
            raise KeyTypeMismatchError(f"key_ref '{name}' is type {user_row.type}, expected {expected_type}")
        if user_row.auth_kind == CredentialAuthKind.OAUTH:
            if not _is_set(user_row.encrypted_value):
                raise KeyNotFoundError(f'credential not connected: {name}')
            from apps.keys.oauth.services import materialize_runtime_credential

            return materialize_runtime_credential(user_row)
        if not _is_set(user_row.encrypted_value):
            raise KeyNotFoundError(f'credential not set: {name}')
        return _decrypt_row(user_row.encrypted_value)
    system_row = SystemCredential.objects.filter(name=name).first()
    if system_row is not None:
        if system_row.type != expected_type:
            raise KeyTypeMismatchError(f"key_ref '{name}' is type {system_row.type}, expected {expected_type}")
        if not _is_set(system_row.encrypted_value):
            raise KeyNotFoundError(f'credential not set: {name}')
        return _decrypt_row(system_row.encrypted_value)
    raise KeyNotFoundError(f'credential not found: {name}')


def make_secret_supplier(
    user_id: int,
    *,
    name: str | None = None,
    type: str,
) -> Callable[[], str | None]:
    """Factory for lazy resolution. Raises on missing/type mismatch when called,
    not when constructed. Preferred wiring primitive for multi-call libs.
    """
    validate_type(type)

    if name is None:

        def _default_supplier() -> str | None:
            return resolve_default_secret(user_id, type)

        return _default_supplier

    def _named_supplier() -> str:
        return resolve_secret(user_id, name, expected_type=type)

    return _named_supplier


def get_llm_default_secret(user_id: int, provider: str) -> str | None:
    """Convenience wrapper — same resolution as ``resolve_default_secret``."""
    return resolve_default_secret(user_id, provider)
