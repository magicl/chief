# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""OAuth declarations, one-time authorization state, and grant lifecycle services."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.bus.resources import publish_resource_update_after_commit
from apps.keys import crypto
from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyStorageMisconfiguredError,
    KeyValidationError,
    OAuthConfigurationError,
    OAuthStateError,
)
from apps.keys.models import (
    CredentialAuthKind,
    CredentialHealthStatus,
    CredentialStatus,
    UserCredential,
)
from apps.keys.oauth.registry import OAUTH_PROVIDERS
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from libs.providers.key.health_codes import (
    INVALID_DECLARATION,
    OAUTH_NOT_CONNECTED,
    UNKNOWN_TYPE,
)

if TYPE_CHECKING:
    from apps.keys.services.queries import KeyMetadata

STATE_SALT = 'chief.keys.oauth.state'
_DEFAULT_STATE_MAX_AGE_SECONDS = 600
_STATE_KEYS = {
    'user_id',
    'credential_id',
    'provider',
    'nonce',
    'session_binding',
    'config_fingerprint',
    'grant_fingerprint',
}


@dataclass(frozen=True, slots=True)
class OAuthStart:
    """Return the provider redirect and its signed one-time callback state."""

    authorization_url: str
    state: str


def _state_max_age() -> int:
    """Return the configured state lifetime with the documented default."""
    return int(getattr(settings, 'OAUTH_STATE_MAX_AGE_SECONDS', _DEFAULT_STATE_MAX_AGE_SECONDS))


def _session_binding(session_key: str) -> str:
    """Hash the current session identifier before placing its binding in state."""
    if not isinstance(session_key, str) or not session_key:
        raise OAuthStateError('OAuth authorization state is invalid')
    return hashlib.sha256(session_key.encode()).hexdigest()


def _state_marker(nonce: str) -> str:
    """Derive a cache key containing only a digest of the random state nonce."""
    return f'keys:oauth-state:{hashlib.sha256(nonce.encode()).hexdigest()}'


def normalize_auth_config(
    *,
    provider_id: str,
    credential_type: str,
    capability_ids: Iterable[str],
) -> dict[str, object]:
    """Validate a provider declaration and return its exact canonical JSON shape."""
    provider = OAUTH_PROVIDERS.get(provider_id)
    if credential_type != provider.credential_type:
        raise KeyValidationError('OAuth provider does not support this credential type')
    capabilities = provider.normalize_capabilities(capability_ids)
    return {'provider': provider.id, 'capabilities': list(capabilities)}


def auth_config_fingerprint(auth_config: Mapping[str, object]) -> str:
    """Hash canonical compact JSON so declaration ordering cannot alter a binding."""
    canonical = json.dumps(dict(auth_config), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _grant_fingerprint(encrypted_value: bytes | memoryview) -> str:
    """Hash ciphertext, including an empty grant, without decrypting credential material."""
    ciphertext = encrypted_value.tobytes() if isinstance(encrypted_value, memoryview) else encrypted_value
    return hashlib.sha256(ciphertext).hexdigest()


def _validated_oauth_declaration(row: UserCredential) -> tuple[str, tuple[str, ...], str]:
    """Validate an active OAuth row and return provider, capabilities, and fingerprint."""
    from apps.keys.services.queries import get_oauth_metadata

    if row.status != CredentialStatus.ACTIVE:
        raise KeyNotFoundError('OAuth credential is not available')
    if row.auth_kind != CredentialAuthKind.OAUTH:
        raise KeyValidationError('Credential is not configured for OAuth')
    provider_id, capability_ids = get_oauth_metadata(row)
    if provider_id is None or not capability_ids:
        raise KeyValidationError('OAuth credential configuration is invalid')
    normalized = normalize_auth_config(
        provider_id=provider_id,
        credential_type=row.type,
        capability_ids=capability_ids,
    )
    if normalized != row.auth_config:
        raise KeyValidationError('OAuth credential configuration is invalid')
    return provider_id, capability_ids, auth_config_fingerprint(normalized)


def _owned_active_oauth(user_id: int, credential_id: uuid.UUID) -> UserCredential:
    """Load one owned active OAuth row without decrypting credential material."""
    from apps.keys.services.queries import get_owned_user_credential

    try:
        row = get_owned_user_credential(user_id, credential_id)
    except KeyNotFoundError:
        raise KeyNotFoundError('OAuth credential not found') from None
    _validated_oauth_declaration(row)
    return row


def _reject_unrecoverable_declaration_health(row: UserCredential) -> None:
    """Block starting consent for a declaration that cannot become usable as-is.

    ``oauth_not_connected`` (and ``ready``) rows may start authorization normally;
    ``invalid_declaration`` / ``unknown_type`` rows need a disk/UI edit first, so
    consent must not begin — the disk YAML fix, not a new grant, resolves them.
    """
    if row.health_code in (INVALID_DECLARATION, UNKNOWN_TYPE):
        raise KeyValidationError('OAuth credential configuration is invalid')


def start_authorization(
    *,
    user_id: int,
    credential_id: uuid.UUID,
    session_key: str,
    redirect_uri: str,
) -> OAuthStart:
    """Create signed session-bound state and a one-use provider authorization URL."""
    row = _owned_active_oauth(user_id, credential_id)
    _reject_unrecoverable_declaration_health(row)
    provider_id, capability_ids, fingerprint = _validated_oauth_declaration(row)
    provider = OAUTH_PROVIDERS.get(provider_id)
    nonce = secrets.token_urlsafe(32)
    marker = _state_marker(nonce)
    if not cache.add(marker, True, timeout=_state_max_age()):
        raise OAuthStateError('OAuth authorization could not be started')

    state: str | None = None
    authorization_url: str | None = None
    failed = False
    configuration_failed = False
    try:
        state = signing.dumps(
            {
                'user_id': user_id,
                'credential_id': str(row.pk),
                'provider': provider_id,
                'nonce': nonce,
                'session_binding': _session_binding(session_key),
                'config_fingerprint': fingerprint,
                'grant_fingerprint': _grant_fingerprint(row.encrypted_value),
            },
            salt=STATE_SALT,
        )
        authorization_url = provider.build_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            capability_ids=capability_ids,
        )
    except OAuthConfigurationError:
        configuration_failed = True
    except Exception:  # pylint: disable=broad-exception-caught
        failed = True
    finally:
        nonce = ''
        marker_to_delete = marker if failed or configuration_failed else None
        marker = ''
        if marker_to_delete is not None:
            cache.delete(marker_to_delete)
        marker_to_delete = None

    if configuration_failed:
        state = None
        authorization_url = None
        raise OAuthConfigurationError('OAuth provider is not configured') from None
    if failed or state is None or authorization_url is None:
        state = None
        authorization_url = None
        raise OAuthStateError('OAuth authorization could not be started') from None
    return OAuthStart(authorization_url=authorization_url, state=state)


def _load_state(state: str) -> dict[str, Any]:
    """Verify state signature and age, returning only the expected payload shape."""
    payload: Any = None
    invalid = False
    try:
        payload = signing.loads(state, salt=STATE_SALT, max_age=_state_max_age())
    except signing.BadSignature:
        invalid = True
    finally:
        state = ''
    if invalid:
        raise OAuthStateError('OAuth authorization state is invalid') from None
    if not isinstance(payload, dict) or set(payload) != _STATE_KEYS:
        raise OAuthStateError('OAuth authorization state is invalid')
    return payload


def _validated_state_values(
    payload: Mapping[str, Any],
    *,
    user_id: int,
    session_key: str,
) -> tuple[uuid.UUID, str, str, str, str]:
    """Return validated credential, provider, nonce, config, and grant fingerprints."""
    if payload.get('user_id') != user_id:
        raise OAuthStateError('OAuth authorization state is invalid')
    if payload.get('session_binding') != _session_binding(session_key):
        raise OAuthStateError('OAuth authorization state is invalid')

    provider_id = payload.get('provider')
    nonce = payload.get('nonce')
    fingerprint = payload.get('config_fingerprint')
    grant_fingerprint = payload.get('grant_fingerprint')
    credential_value = payload.get('credential_id')
    if (
        not isinstance(provider_id, str)
        or not provider_id
        or not isinstance(nonce, str)
        or not nonce
        or not isinstance(fingerprint, str)
        or not fingerprint
        or not isinstance(grant_fingerprint, str)
        or not grant_fingerprint
        or not isinstance(credential_value, str)
        or not credential_value
    ):
        raise OAuthStateError('OAuth authorization state is invalid')
    try:
        credential_id = uuid.UUID(credential_value)
    except (ValueError, TypeError, AttributeError):
        raise OAuthStateError('OAuth authorization state is invalid') from None
    return credential_id, provider_id, nonce, fingerprint, grant_fingerprint


def _verify_state_row(
    row: UserCredential,
    *,
    provider_id: str,
    fingerprint: str,
    grant_fingerprint: str,
) -> tuple[str, ...]:
    """Recheck declaration and ciphertext baselines against the current row."""
    try:
        current_provider, capability_ids, current_fingerprint = _validated_oauth_declaration(row)
    except (KeyNotFoundError, KeyValidationError):
        raise OAuthStateError('OAuth authorization state is invalid') from None
    if (
        current_provider != provider_id
        or current_fingerprint != fingerprint
        or _grant_fingerprint(row.encrypted_value) != grant_fingerprint
    ):
        raise OAuthStateError('OAuth authorization state is invalid')
    return capability_ids


def complete_authorization(
    *,
    user_id: int,
    session_key: str,
    state: str,
    code: str | None,
    redirect_uri: str,
) -> KeyMetadata:
    """Consume callback state before exchange and atomically replace a validated grant."""
    payload: dict[str, Any] | None = None
    try:
        payload = _load_state(state)
        return _complete_authorization_payload(
            user_id=user_id,
            session_key=session_key,
            payload=payload,
            code=code,
            redirect_uri=redirect_uri,
        )
    finally:
        state = ''
        code = None
        payload = None


def _complete_authorization_payload(
    *,
    user_id: int,
    session_key: str,
    payload: Mapping[str, Any],
    code: str | None,
    redirect_uri: str,
) -> KeyMetadata:
    """Complete validated state, set health to ready, clearing callback/grant locals on exit."""
    from apps.keys.services.queries import _user_metadata

    grant_payload: str | None = None
    encrypted_grant: bytes | None = None
    completed_row: UserCredential | None = None
    try:
        credential_id, provider_id, nonce, fingerprint, grant_fingerprint = _validated_state_values(
            payload,
            user_id=user_id,
            session_key=session_key,
        )
        try:
            row = _owned_active_oauth(user_id, credential_id)
        except (KeyNotFoundError, KeyValidationError):
            raise OAuthStateError('OAuth authorization state is invalid') from None
        capability_ids = _verify_state_row(
            row,
            provider_id=provider_id,
            fingerprint=fingerprint,
            grant_fingerprint=grant_fingerprint,
        )
        marker = _state_marker(nonce)
        if not cache.delete(marker):
            raise OAuthStateError('OAuth authorization state is invalid')
        if code is None:
            return _user_metadata(row)
        if not isinstance(code, str) or not code:
            raise OAuthStateError('OAuth authorization state is invalid')

        provider = OAUTH_PROVIDERS.get(provider_id)
        grant_payload = provider.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            capability_ids=capability_ids,
        )
        with transaction.atomic():
            try:
                locked_row = UserCredential.objects.select_for_update().get(
                    pk=credential_id,
                    user_id=user_id,
                    status=CredentialStatus.ACTIVE,
                )
            except UserCredential.DoesNotExist:
                raise OAuthStateError('OAuth authorization state is invalid') from None
            _verify_state_row(
                locked_row,
                provider_id=provider_id,
                fingerprint=fingerprint,
                grant_fingerprint=grant_fingerprint,
            )
            encryption_failed = False
            try:
                encrypted_grant = crypto.encrypt(grant_payload)
            except Exception:  # pylint: disable=broad-exception-caught
                encryption_failed = True
            if encryption_failed:
                grant_payload = None
                encrypted_grant = None
                raise KeyStorageMisconfiguredError('credential storage misconfigured') from None
            locked_row.encrypted_value = encrypted_grant
            if encrypted_grant:
                locked_row.health_status = CredentialHealthStatus.READY
                locked_row.health_code = ''
            else:
                locked_row.health_status = CredentialHealthStatus.NEEDS_ATTENTION
                locked_row.health_code = OAUTH_NOT_CONNECTED
            locked_row.save(update_fields=['encrypted_value', 'health_status', 'health_code', 'updated_at'])
            publish_resource_update_after_commit(user_id, 'keys')
            completed_row = locked_row
        return _user_metadata(completed_row)
    finally:
        code = None
        grant_payload = None
        encrypted_grant = None
        completed_row = None


def disconnect_authorization(*, user_id: int, credential_id: uuid.UUID) -> KeyMetadata:
    """Clear an owned active OAuth grant and mark it needs_attention/oauth_not_connected."""
    from apps.keys.services.queries import _user_metadata

    with transaction.atomic():
        try:
            row = UserCredential.objects.select_for_update().get(
                pk=credential_id,
                user_id=user_id,
                status=CredentialStatus.ACTIVE,
            )
        except UserCredential.DoesNotExist:
            raise KeyNotFoundError('OAuth credential not found') from None
        _validated_oauth_declaration(row)
        row.encrypted_value = b''
        row.health_status = CredentialHealthStatus.NEEDS_ATTENTION
        row.health_code = OAUTH_NOT_CONNECTED
        row.save(update_fields=['encrypted_value', 'health_status', 'health_code', 'updated_at'])
        publish_resource_update_after_commit(user_id, 'keys')
    return _user_metadata(row)


def materialize_runtime_credential(row: UserCredential) -> str:
    """Decrypt and materialize one OAuth grant, scrubbing operational failure state."""
    from apps.keys.services.queries import _as_bytes

    provider_id, capability_ids, _ = _validated_oauth_declaration(row)
    provider = OAUTH_PROVIDERS.get(provider_id)
    grant_payload: str | None = None
    runtime_payload: str | None = None
    failed = False
    try:
        grant_payload = crypto.decrypt(_as_bytes(row.encrypted_value))
        runtime_payload = provider.materialize_runtime(
            grant_payload=grant_payload,
            capability_ids=capability_ids,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        failed = True
    finally:
        grant_payload = None

    if failed or runtime_payload is None:
        runtime_payload = None
        raise KeyValidationError('OAuth credential could not be resolved') from None
    return runtime_payload
