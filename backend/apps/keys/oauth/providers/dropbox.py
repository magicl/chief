# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dropbox OAuth capability catalog, code exchange, and grant materialization."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

import httpx
from apps.keys.exceptions import (
    KeyValidationError,
    OAuthConfigurationError,
    OAuthGrantError,
    OAuthProviderError,
)
from apps.keys.oauth.types import OAuthCapability
from django.conf import settings
from libs.dropbox_scopes import FILES_METADATA_READ_SCOPE

AUTHORIZATION_ENDPOINT = 'https://www.dropbox.com/oauth2/authorize'
TOKEN_ENDPOINT = 'https://api.dropboxapi.com/oauth2/token'  # nosec B105 -- public endpoint, not a credential
_TOKEN_TIMEOUT_SECONDS = 10.0

DROPBOX_CAPABILITIES: tuple[OAuthCapability, ...] = (
    OAuthCapability(
        id='files_metadata',
        label='Read Dropbox metadata',
        description='list/search file and folder names and metadata without downloading content.',
        scope=FILES_METADATA_READ_SCOPE,
        support='current',
    ),
)


def _load_app_credentials() -> tuple[str, str]:
    """Load operation-local Dropbox app credentials, rejecting unset/non-string values."""
    app_key: Any = None
    app_secret: Any = None
    credentials: tuple[str, str] | None = None
    try:
        app_key = getattr(settings, 'DROPBOX_OAUTH_APP_KEY', '')
        app_secret = getattr(settings, 'DROPBOX_OAUTH_APP_SECRET', '')
        if isinstance(app_key, str) and app_key.strip() and isinstance(app_secret, str) and app_secret.strip():
            credentials = (app_key, app_secret)
    finally:
        app_key = None
        app_secret = None
    if credentials is None:
        raise OAuthConfigurationError('Dropbox OAuth is not configured')
    return credentials


def _compact_json(payload: dict[str, Any]) -> str:
    """Serialize a provider payload canonically without logging or retaining a copy."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


class DropboxOAuthProvider:
    """Implement Dropbox's allowlisted OAuth flow without retaining operation secrets."""

    id = 'dropbox'
    credential_type = 'dropbox'
    capabilities = DROPBOX_CAPABILITIES

    def normalize_capabilities(self, capability_ids: Iterable[str]) -> tuple[str, ...]:
        """Validate IDs and return a deduplicated tuple in catalog order."""
        supplied = tuple(capability_ids)
        if not supplied:
            raise KeyValidationError('At least one Dropbox OAuth capability is required')
        if any(not isinstance(capability_id, str) or not capability_id.strip() for capability_id in supplied):
            raise KeyValidationError('Invalid Dropbox OAuth capability')
        selected = set(supplied)
        catalog_ids = {capability.id for capability in self.capabilities}
        if not selected.issubset(catalog_ids):
            raise KeyValidationError('Invalid Dropbox OAuth capability')
        return tuple(capability.id for capability in self.capabilities if capability.id in selected)

    def _requested_scopes(self, capability_ids: Iterable[str]) -> tuple[str, ...]:
        """Expand validated capability IDs to exact scopes in stable catalog order."""
        normalized = self.normalize_capabilities(capability_ids)
        selected = set(normalized)
        return tuple(capability.scope for capability in self.capabilities if capability.id in selected)

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Build Dropbox's fixed consent URL using lazily loaded app credentials."""
        app_key: str | None = None
        app_secret: str | None = None  # pylint: disable=unused-variable
        query: str | None = None
        try:
            scopes = self._requested_scopes(capability_ids)
            app_key, app_secret = _load_app_credentials()
            query = urlencode(
                {
                    'client_id': app_key,
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'scope': ' '.join(scopes),
                    'state': state,
                    'token_access_type': 'offline',  # nosec B105 -- Dropbox consent parameter, not a credential
                }
            )
            return f'{AUTHORIZATION_ENDPOINT}?{query}'
        finally:
            state = ''
            app_key = None
            app_secret = None
            query = None

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Exchange a code and return a minimal grant after complete-scope validation."""
        requested_scopes: tuple[str, ...] = ()
        app_key: str | None = None
        app_secret: str | None = None
        response: Any = None
        token_payload: Any = None
        refresh_token: Any = None
        granted_scope_value: Any = None
        granted_scopes: set[str] | None = None
        serialized_grant: str | None = None
        failed = False
        try:
            requested_scopes = self._requested_scopes(capability_ids)
            app_key, app_secret = _load_app_credentials()
            try:
                response = httpx.post(
                    TOKEN_ENDPOINT,
                    data={
                        'client_id': app_key,
                        'client_secret': app_secret,
                        'code': code,
                        'grant_type': 'authorization_code',
                        'redirect_uri': redirect_uri,
                    },
                    timeout=_TOKEN_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                token_payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError):
                failed = True

            if not failed:
                if not isinstance(token_payload, dict) or 'error' in token_payload:
                    failed = True
                else:
                    refresh_token = token_payload.get('refresh_token')
                    granted_scope_value = token_payload.get('scope')
                    if not isinstance(refresh_token, str) or not refresh_token.strip():
                        failed = True
                    elif not isinstance(granted_scope_value, str) or not granted_scope_value.strip():
                        failed = True
                    else:
                        granted_scopes = set(granted_scope_value.split())
                        if not set(requested_scopes).issubset(granted_scopes):
                            failed = True
                        else:
                            serialized_grant = _compact_json(
                                {
                                    'version': 1,
                                    'refresh_token': refresh_token,
                                    'granted_scopes': list(requested_scopes),
                                }
                            )
        finally:
            app_key = None
            app_secret = None
            code = ''
            response = None
            token_payload = None
            refresh_token = None
            granted_scope_value = None
            granted_scopes = None

        if failed or serialized_grant is None:
            raise OAuthProviderError('Dropbox OAuth code exchange failed')
        return serialized_grant

    def materialize_runtime(self, *, grant_payload: str, capability_ids: tuple[str, ...]) -> str:
        """Validate a stored grant and return one operation-local runtime envelope."""
        requested_scopes: tuple[str, ...] = ()
        app_key: str | None = None
        app_secret: str | None = None
        grant: Any = None
        version: Any = None
        refresh_token: Any = None
        granted_scopes: Any = None
        runtime_payload: str | None = None
        invalid = False
        try:
            requested_scopes = self._requested_scopes(capability_ids)
            app_key, app_secret = _load_app_credentials()
            try:
                grant = json.loads(grant_payload)
            except (json.JSONDecodeError, TypeError):
                invalid = True

            expected_keys = {'version', 'refresh_token', 'granted_scopes'}
            if not invalid:
                if not isinstance(grant, dict) or set(grant) != expected_keys:
                    invalid = True
                else:
                    version = grant.get('version')
                    refresh_token = grant.get('refresh_token')
                    granted_scopes = grant.get('granted_scopes')
                    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
                        invalid = True
                    elif not isinstance(refresh_token, str) or not refresh_token.strip():
                        invalid = True
                    elif (
                        not isinstance(granted_scopes, list)
                        or any(not isinstance(scope, str) for scope in granted_scopes)
                        or granted_scopes != list(requested_scopes)
                    ):
                        invalid = True
                    else:
                        runtime_payload = _compact_json(
                            {
                                'chief_dropbox_oauth': 1,
                                'app_key': app_key,
                                'app_secret': app_secret,
                                'refresh_token': refresh_token,
                                'scopes': granted_scopes,
                            }
                        )
        finally:
            grant_payload = ''
            app_key = None
            app_secret = None
            grant = None
            version = None
            refresh_token = None
            granted_scopes = None

        if invalid or runtime_payload is None:
            raise OAuthGrantError('Stored Dropbox OAuth grant is invalid')
        return runtime_payload


DROPBOX_OAUTH_PROVIDER = DropboxOAuthProvider()
