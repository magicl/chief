# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Build operation-local Google credentials without Django dependencies."""

from __future__ import annotations

import json
from typing import Any, cast

from google.auth.credentials import Credentials
from libs.google_scopes import GOOGLE_OAUTH_SCOPES as _CANONICAL_GOOGLE_OAUTH_SCOPES

GOOGLE_OAUTH_SCOPES = _CANONICAL_GOOGLE_OAUTH_SCOPES

_OAUTH_FIELDS = frozenset(
    {
        'chief_google_oauth',
        'client_id',
        'client_secret',
        'refresh_token',
        'scopes',
        'token_uri',
    }
)
_GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'  # nosec B105 - fixed Google public endpoint.


class GoogleCredentialError(ValueError):
    """Report a safe Google credential validation or construction failure."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting every duplicate key."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def build_google_credentials(
    raw_credential: str,
    *,
    service_account_scopes: tuple[str, ...],
    subject: str | None,
    require_service_account_subject: bool,
) -> Credentials:
    """Build operation-local service-account or Chief OAuth credentials.

    The exact integer OAuth sentinel selects the runtime envelope. All parsed
    secrets and vendor objects are cleared before a safe failure can escape.
    """
    parsed: Any = None
    marker: Any = None
    scopes: Any = None
    built: Any = None
    failure_message: str | None = None
    construction_failed = False
    is_oauth = False
    try:
        if not isinstance(raw_credential, str):
            failure_message = 'Google credential must be a JSON string'
        else:
            try:
                parsed = json.loads(raw_credential, object_pairs_hook=_unique_json_object)
            except (RecursionError, TypeError, ValueError):
                failure_message = 'Google credential is not valid JSON'

        if failure_message is None and not isinstance(parsed, dict):
            failure_message = 'Google credential must be a JSON object'

        if failure_message is None:
            marker = parsed.get('chief_google_oauth')
            if 'chief_google_oauth' in parsed:
                if isinstance(marker, int) and not isinstance(marker, bool) and marker == 1:
                    is_oauth = True
                else:
                    failure_message = 'Google credential has an invalid OAuth version'

        if failure_message is None and is_oauth:
            string_fields = ('client_id', 'client_secret', 'refresh_token', 'token_uri')
            scopes = parsed.get('scopes')
            if (
                set(parsed) != _OAUTH_FIELDS
                or any(not isinstance(parsed.get(field), str) or not parsed[field].strip() for field in string_fields)
                or not isinstance(scopes, list)
                or not scopes
                or any(not isinstance(scope, str) or not scope.strip() for scope in scopes)
                or len(scopes) != len(set(scopes))
                or any(scope not in GOOGLE_OAUTH_SCOPES for scope in scopes)
                or parsed.get('token_uri') != _GOOGLE_TOKEN_URI
            ):
                failure_message = 'Google OAuth credential is invalid'
            else:
                try:
                    from google.oauth2 import (  # noqa: PLC0415
                        credentials as oauth_credentials,
                    )

                    built = oauth_credentials.Credentials(  # type: ignore[no-untyped-call]
                        token=None,
                        refresh_token=parsed['refresh_token'],
                        token_uri=parsed['token_uri'],
                        client_id=parsed['client_id'],
                        client_secret=parsed['client_secret'],
                        scopes=parsed['scopes'],
                    )
                    return cast(Credentials, built)
                except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                    construction_failed = True

        if failure_message is None and not is_oauth:
            if parsed.get('type') != 'service_account':
                failure_message = 'Google credential must be a service-account object'
            elif (
                not isinstance(service_account_scopes, tuple)
                or not service_account_scopes
                or any(not isinstance(scope, str) or not scope.strip() for scope in service_account_scopes)
            ):
                failure_message = 'Google service-account scopes are invalid'
            elif subject is not None and (not isinstance(subject, str) or not subject.strip()):
                failure_message = 'Google service-account subject is invalid'
            elif require_service_account_subject and subject is None:
                failure_message = 'Google service-account subject is required'
            else:
                try:
                    from google.oauth2 import service_account  # noqa: PLC0415

                    built = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                        parsed,
                        scopes=service_account_scopes,
                    )
                    if subject is not None:
                        built = built.with_subject(subject)
                    return cast(Credentials, built)
                except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                    construction_failed = True
    finally:
        # Propagated tracebacks retain frame locals, including function arguments.
        raw_credential = ''
        parsed = None
        marker = None
        scopes = None
        built = None
        subject = None

    if construction_failed:
        raise GoogleCredentialError('failed to build Google credentials') from None
    raise GoogleCredentialError(failure_message or 'Google credential is invalid') from None
