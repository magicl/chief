# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Google OAuth capability catalog, code exchange, and grant materialization."""

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
from libs.google_scopes import (
    DOCUMENTS_READONLY_SCOPE,
    DOCUMENTS_SCOPE,
    DRIVE_FILE_SCOPE,
    DRIVE_METADATA_READONLY_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    SPREADSHEETS_READONLY_SCOPE,
    SPREADSHEETS_SCOPE,
)

AUTHORIZATION_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'  # nosec B105 -- public endpoint, not a credential
_TOKEN_TIMEOUT_SECONDS = 10.0

GOOGLE_CAPABILITIES: tuple[OAuthCapability, ...] = (
    OAuthCapability(
        id='gmail_read',
        label='Read Gmail',
        description='view messages and Gmail settings without changing or sending mail.',
        scope=GMAIL_READONLY_SCOPE,
        support='current',
    ),
    OAuthCapability(
        id='gmail_modify',
        label='Manage Gmail',
        description=(
            'read mail, change labels/archive/trash, and compose/send mail. ' 'Google includes sending in this scope.'
        ),
        scope=GMAIL_MODIFY_SCOPE,
        support='current',
    ),
    OAuthCapability(
        id='gmail_send',
        label='Send Gmail',
        description='send mail without granting mailbox read access.',
        scope=GMAIL_SEND_SCOPE,
        support='current',
    ),
    OAuthCapability(
        id='drive_metadata',
        label='Read Drive metadata',
        description='list/search file names and metadata without downloading content.',
        scope=DRIVE_METADATA_READONLY_SCOPE,
        support='current',
    ),
    OAuthCapability(
        id='drive_read',
        label='Read Drive files',
        description='search, view, and download all visible Drive files without changing them.',
        scope=DRIVE_READONLY_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='drive_file',
        label='Manage selected Drive files',
        description=(
            'create or modify only files opened with or explicitly shared with Chief; '
            'a future Google Picker/share flow is required.'
        ),
        scope=DRIVE_FILE_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='drive_manage',
        label='Manage all Drive files',
        description='search, read, create, update, move, and delete all visible Drive files.',
        scope=DRIVE_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='docs_read',
        label='Read Google Docs',
        description='read all visible Google Docs documents.',
        scope=DOCUMENTS_READONLY_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='docs_write',
        label='Manage Google Docs',
        description='read, create, edit, and delete all visible Google Docs documents.',
        scope=DOCUMENTS_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='sheets_read',
        label='Read Google Sheets',
        description='read all visible spreadsheets.',
        scope=SPREADSHEETS_READONLY_SCOPE,
        support='future',
    ),
    OAuthCapability(
        id='sheets_write',
        label='Manage Google Sheets',
        description='read, create, edit, and delete all visible spreadsheets.',
        scope=SPREADSHEETS_SCOPE,
        support='future',
    ),
)


def _load_client_credentials() -> tuple[str, str]:
    """Load operation-local Google app credentials, rejecting unset/non-string values."""
    client_id: Any = None
    client_secret: Any = None
    credentials: tuple[str, str] | None = None
    try:
        client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
        client_secret = getattr(settings, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
        if (
            isinstance(client_id, str)
            and client_id.strip()
            and isinstance(client_secret, str)
            and client_secret.strip()
        ):
            credentials = (client_id, client_secret)
    finally:
        client_id = None
        client_secret = None
    if credentials is None:
        raise OAuthConfigurationError('Google OAuth is not configured')
    return credentials


def _compact_json(payload: dict[str, Any]) -> str:
    """Serialize a provider payload canonically without logging or retaining a copy."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


class GoogleOAuthProvider:
    """Implement Google's allowlisted OAuth flow without retaining operation secrets."""

    id = 'google'
    credential_type = 'google'
    capabilities = GOOGLE_CAPABILITIES

    def normalize_capabilities(self, capability_ids: Iterable[str]) -> tuple[str, ...]:
        """Validate IDs and return a deduplicated tuple in catalog order."""
        supplied = tuple(capability_ids)
        if not supplied:
            raise KeyValidationError('At least one Google OAuth capability is required')
        if any(not isinstance(capability_id, str) or not capability_id.strip() for capability_id in supplied):
            raise KeyValidationError('Invalid Google OAuth capability')
        selected = set(supplied)
        catalog_ids = {capability.id for capability in self.capabilities}
        if not selected.issubset(catalog_ids):
            raise KeyValidationError('Invalid Google OAuth capability')
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
        """Build Google's fixed consent URL using lazily loaded app credentials."""
        client_id: str | None = None
        client_secret: str | None = None  # pylint: disable=unused-variable
        query: str | None = None
        try:
            scopes = self._requested_scopes(capability_ids)
            client_id, client_secret = _load_client_credentials()
            query = urlencode(
                {
                    'access_type': 'offline',
                    'client_id': client_id,
                    'prompt': 'consent',
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'scope': ' '.join(scopes),
                    'state': state,
                }
            )
            return f'{AUTHORIZATION_ENDPOINT}?{query}'
        finally:
            state = ''
            client_id = None
            client_secret = None
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
        client_id: str | None = None
        client_secret: str | None = None
        response: Any = None
        token_payload: Any = None
        refresh_token: Any = None
        granted_scope_value: Any = None
        granted_scopes: set[str] | None = None
        serialized_grant: str | None = None
        failed = False
        try:
            requested_scopes = self._requested_scopes(capability_ids)
            client_id, client_secret = _load_client_credentials()
            try:
                response = httpx.post(
                    TOKEN_ENDPOINT,
                    data={
                        'client_id': client_id,
                        'client_secret': client_secret,
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
                    elif not isinstance(granted_scope_value, str):
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
            client_id = None
            client_secret = None
            code = ''
            response = None
            token_payload = None
            refresh_token = None
            granted_scope_value = None
            granted_scopes = None

        if failed or serialized_grant is None:
            raise OAuthProviderError('Google OAuth code exchange failed')
        return serialized_grant

    def materialize_runtime(self, *, grant_payload: str, capability_ids: tuple[str, ...]) -> str:
        """Validate a stored grant and return one operation-local runtime envelope."""
        requested_scopes: tuple[str, ...] = ()
        client_id: str | None = None
        client_secret: str | None = None
        grant: Any = None
        version: Any = None
        refresh_token: Any = None
        granted_scopes: Any = None
        runtime_payload: str | None = None
        invalid = False
        try:
            requested_scopes = self._requested_scopes(capability_ids)
            client_id, client_secret = _load_client_credentials()
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
                                'chief_google_oauth': 1,
                                'client_id': client_id,
                                'client_secret': client_secret,
                                'refresh_token': refresh_token,
                                'scopes': granted_scopes,
                                'token_uri': TOKEN_ENDPOINT,
                            }
                        )
        finally:
            grant_payload = ''
            client_id = None
            client_secret = None
            grant = None
            version = None
            refresh_token = None
            granted_scopes = None

        if invalid or runtime_payload is None:
            raise OAuthGrantError('Stored Google OAuth grant is invalid')
        return runtime_payload


GOOGLE_OAUTH_PROVIDER = GoogleOAuthProvider()
