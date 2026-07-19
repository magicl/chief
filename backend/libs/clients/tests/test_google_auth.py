# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the shared Django-free Google credential builder."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import ModuleType
from typing import Any, cast
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials as OAuthCredentials
from libs.clients.google_auth import GoogleCredentialError, build_google_credentials

from olib.py.django.test.cases import OTestCase

OAUTH_FIELDS = {
    'chief_google_oauth': 1,
    'client_id': 'client-id-secret-sentinel',
    'client_secret': 'client-secret-sentinel',
    'refresh_token': 'refresh-token-sentinel',
    'scopes': [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
    ],
    'token_uri': 'https://oauth2.googleapis.com/token',
}

APPROVED_GOOGLE_SCOPES = (
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
)


def _retained_google_auth_locals(failure: BaseException) -> str:
    """Render locals retained by shared credential-builder traceback frames."""
    retained: list[dict[str, Any]] = []
    traceback = failure.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get('__name__') == 'libs.clients.google_auth':
            retained.append(dict(traceback.tb_frame.f_locals))
        traceback = traceback.tb_next
    return repr(retained)


def _retained_google_auth_values(failure: BaseException) -> list[object]:
    """Collect direct values retained by shared credential-builder frames."""
    retained: list[object] = []
    traceback = failure.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get('__name__') == 'libs.clients.google_auth':
            retained.extend(traceback.tb_frame.f_locals.values())
        traceback = traceback.tb_next
    return retained


def _reachable_failure_objects(value: object, *, seen: set[int] | None = None, depth: int = 0) -> list[object]:
    """Traverse safe failure state, including helper traceback locals and object fields."""
    if seen is None:
        seen = set()
    if depth > 8 or id(value) in seen:
        return []
    seen.add(id(value))
    reachable = [value]
    nested: list[object] = []
    if isinstance(value, BaseException):
        nested.extend(value.args)
        if value.__context__ is not None:
            nested.append(value.__context__)
        if value.__cause__ is not None:
            nested.append(value.__cause__)
        traceback = value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_globals.get('__name__') == 'libs.clients.google_auth':
                nested.extend(traceback.tb_frame.f_locals.values())
            traceback = traceback.tb_next
    elif isinstance(value, Mapping):
        nested.extend(value.keys())
        nested.extend(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        nested.extend(value)
    elif not isinstance(value, ModuleType) and not callable(value) and hasattr(value, '__dict__'):
        nested.append(vars(value))
    for item in nested:
        reachable.extend(_reachable_failure_objects(item, seen=seen, depth=depth + 1))
    return reachable


class TestBuildGoogleCredentials(OTestCase):
    """Exercise strict envelope selection, validation, and secret lifetime."""

    @patch('google.oauth2.service_account.Credentials.from_service_account_info')
    def test_service_account_keeps_scopes_and_delegation(self, from_info: MagicMock) -> None:
        """Build service-account credentials with the caller's scopes and subject."""
        credentials = MagicMock()
        delegated = MagicMock()
        credentials.with_subject.return_value = delegated
        from_info.return_value = credentials
        info = {'type': 'service_account', 'client_email': 'sa@example.com'}

        result = build_google_credentials(
            json.dumps(info),
            service_account_scopes=('scope-one', 'scope-two'),
            subject='user@example.com',
            require_service_account_subject=True,
        )

        self.assertIs(result, delegated)
        from_info.assert_called_once_with(info, scopes=('scope-one', 'scope-two'))
        credentials.with_subject.assert_called_once_with('user@example.com')

    @patch('google.oauth2.service_account.Credentials.from_service_account_info')
    def test_service_account_allows_drive_without_delegation(self, from_info: MagicMock) -> None:
        """Leave Drive credentials undelegated when no subject is configured."""
        credentials = MagicMock()
        from_info.return_value = credentials

        result = build_google_credentials(
            '{"type":"service_account"}',
            service_account_scopes=('drive-scope',),
            subject=None,
            require_service_account_subject=False,
        )

        self.assertIs(result, credentials)
        credentials.with_subject.assert_not_called()

    @patch('google.oauth2.service_account.Credentials.from_service_account_info')
    @patch('libs.clients.google_auth.json.loads')
    def test_service_account_constructor_failure_scrubs_all_reachable_secret_state(
        self,
        loads: MagicMock,
        from_info: MagicMock,
    ) -> None:
        """Remove raw, parsed, private-key, and vendor detail from propagated state."""
        private_key = 'service-account-private-key-secret-sentinel'
        vendor_detail = 'service-account-vendor-detail-secret-sentinel'
        raw = json.dumps({'type': 'service_account', 'private_key': private_key})
        parsed = {'type': 'service_account', 'private_key': private_key}
        loads.return_value = parsed
        from_info.side_effect = RuntimeError(vendor_detail)

        try:
            build_google_credentials(
                raw,
                service_account_scopes=('scope',),
                subject=None,
                require_service_account_subject=False,
            )
        except GoogleCredentialError as failure:
            reachable = _reachable_failure_objects(failure)
            self.assertFalse(any(value is parsed for value in reachable))
            retained = repr(reachable)
            for sentinel in (raw, private_key, vendor_detail):
                self.assertNotIn(sentinel, retained)
                self.assertNotIn(sentinel, str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('Service-account constructor failure did not raise GoogleCredentialError')

    def test_service_account_requires_subject_when_requested(self) -> None:
        """Reject Gmail service-account use without a delegated mailbox."""
        with self.assertRaisesMessage(GoogleCredentialError, 'Google service-account subject is required'):
            build_google_credentials(
                '{"type":"service_account"}',
                service_account_scopes=('gmail-scope',),
                subject=None,
                require_service_account_subject=True,
            )

    def test_oauth_builds_refresh_credentials_from_exact_envelope(self) -> None:
        """Use operation-local OAuth grant fields and ignore any configured subject."""
        result = build_google_credentials(
            json.dumps(OAUTH_FIELDS),
            service_account_scopes=('service-account-only-scope',),
            subject='ignored@example.com',
            require_service_account_subject=True,
        )

        self.assertIsInstance(result, OAuthCredentials)
        oauth_result = cast(OAuthCredentials, result)
        self.assertIsNone(oauth_result.token)
        self.assertEqual(oauth_result.refresh_token, OAUTH_FIELDS['refresh_token'])
        self.assertEqual(oauth_result.client_id, OAUTH_FIELDS['client_id'])
        self.assertEqual(oauth_result.client_secret, OAUTH_FIELDS['client_secret'])
        self.assertEqual(oauth_result.token_uri, OAUTH_FIELDS['token_uri'])
        self.assertEqual(oauth_result.scopes, OAUTH_FIELDS['scopes'])

    def test_oauth_rejects_extras_malformed_secrets_and_empty_scopes(self) -> None:
        """Require the exact versioned envelope with non-empty strings and scopes."""
        malformed = (
            {**OAUTH_FIELDS, 'extra': 'value'},
            {**OAUTH_FIELDS, 'client_id': 1},
            {**OAUTH_FIELDS, 'client_secret': ''},
            {**OAUTH_FIELDS, 'refresh_token': None},
            {**OAUTH_FIELDS, 'token_uri': '  '},
            {**OAUTH_FIELDS, 'scopes': []},
            {**OAUTH_FIELDS, 'scopes': ['valid', '']},
            {**OAUTH_FIELDS, 'scopes': 'valid'},
        )
        for envelope in malformed:
            with self.subTest(envelope=envelope), self.assertRaises(GoogleCredentialError):
                build_google_credentials(
                    json.dumps(envelope),
                    service_account_scopes=('scope',),
                    subject=None,
                    require_service_account_subject=False,
                )

    def test_oauth_accepts_each_provider_approved_scope(self) -> None:
        """Accept every exact Google scope URL approved by the provider design."""
        for scope in APPROVED_GOOGLE_SCOPES:
            envelope = {**OAUTH_FIELDS, 'scopes': [scope]}
            with self.subTest(scope=scope):
                credentials = build_google_credentials(
                    json.dumps(envelope),
                    service_account_scopes=('service-account-scope',),
                    subject=None,
                    require_service_account_subject=False,
                )
                self.assertEqual(cast(OAuthCredentials, credentials).scopes, [scope])

    def test_oauth_rejects_forged_token_uri_unknown_and_duplicate_scopes(self) -> None:
        """Block SSRF endpoints and scopes outside the unique provider allowlist."""
        malformed = (
            {**OAUTH_FIELDS, 'token_uri': 'https://attacker.example/token'},
            {**OAUTH_FIELDS, 'scopes': ['https://attacker.example/private-scope']},
            {
                **OAUTH_FIELDS,
                'scopes': [
                    'https://www.googleapis.com/auth/gmail.readonly',
                    'https://www.googleapis.com/auth/gmail.readonly',
                ],
            },
        )
        for envelope in malformed:
            raw = json.dumps(envelope)
            with self.subTest(envelope=envelope):
                try:
                    build_google_credentials(
                        raw,
                        service_account_scopes=('scope',),
                        subject=None,
                        require_service_account_subject=False,
                    )
                except GoogleCredentialError as failure:
                    retained = repr(_reachable_failure_objects(failure))
                    for sentinel in (
                        raw,
                        'https://attacker.example/token',
                        'https://attacker.example/private-scope',
                    ):
                        self.assertNotIn(sentinel, retained)
                        self.assertNotIn(sentinel, str(failure))
                    self.assertIsNone(failure.__cause__)
                    self.assertIsNone(failure.__context__)
                else:
                    self.fail('Unsafe OAuth endpoint or scope was accepted')

    @patch('google.oauth2.credentials.Credentials')
    def test_oauth_rejects_duplicate_json_object_keys_before_construction(self, credentials: MagicMock) -> None:
        """Reject ambiguous JSON objects without retaining the raw duplicate-key envelope."""
        raw = (
            '{"chief_google_oauth":1,"client_id":"first","client_id":"second",'
            '"client_secret":"secret","refresh_token":"refresh",'
            '"scopes":["https://www.googleapis.com/auth/gmail.readonly"],'
            '"token_uri":"https://oauth2.googleapis.com/token"}'
        )
        try:
            build_google_credentials(
                raw,
                service_account_scopes=('scope',),
                subject=None,
                require_service_account_subject=False,
            )
        except GoogleCredentialError as failure:
            self.assertNotIn(raw, repr(_reachable_failure_objects(failure)))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('Duplicate Google credential JSON keys were accepted')
        credentials.assert_not_called()

    def test_rejects_boolean_and_unknown_oauth_versions_safely(self) -> None:
        """Never let Python's bool-as-int behavior select the OAuth path."""
        for version in (True, False, 2, '1'):
            envelope = {**OAUTH_FIELDS, 'chief_google_oauth': version}
            with self.subTest(version=version), self.assertRaises(GoogleCredentialError) as caught:
                build_google_credentials(
                    json.dumps(envelope),
                    service_account_scopes=('scope',),
                    subject=None,
                    require_service_account_subject=False,
                )
            self.assertNotIn(str(OAUTH_FIELDS['refresh_token']), str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

    @patch('libs.clients.google_auth.json.loads')
    def test_rejected_envelope_clears_parsed_dict_and_access_token(self, loads: MagicMock) -> None:
        """Release the parsed object and forbidden access token after strict validation."""
        parsed = {**OAUTH_FIELDS, 'access_token': 'access-token-secret-sentinel'}
        loads.return_value = parsed

        try:
            build_google_credentials(
                'raw-envelope-secret-sentinel',
                service_account_scopes=('scope',),
                subject=None,
                require_service_account_subject=False,
            )
        except GoogleCredentialError as failure:
            retained_values = _retained_google_auth_values(failure)
            self.assertFalse(any(value is parsed for value in retained_values))
            retained = repr(retained_values)
            for sentinel in (
                'raw-envelope-secret-sentinel',
                'access-token-secret-sentinel',
                OAUTH_FIELDS['refresh_token'],
                OAUTH_FIELDS['client_secret'],
            ):
                self.assertNotIn(str(sentinel), retained)
                self.assertNotIn(str(sentinel), str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('Strict OAuth validation did not reject an access token')

    def test_non_oauth_requires_service_account_shape_and_nonempty_scopes(self) -> None:
        """Reject arbitrary JSON objects and unusable service-account scope tuples."""
        cases = (
            ('{}', ('scope',)),
            ('[]', ('scope',)),
            ('{"type":"authorized_user"}', ('scope',)),
            ('{"type":"service_account"}', ()),
            ('{"type":"service_account"}', ('',)),
        )
        for raw, scopes in cases:
            with self.subTest(raw=raw, scopes=scopes), self.assertRaises(GoogleCredentialError):
                build_google_credentials(
                    raw,
                    service_account_scopes=scopes,
                    subject=None,
                    require_service_account_subject=False,
                )

    @patch('google.oauth2.credentials.Credentials', side_effect=RuntimeError('provider-body-secret-sentinel'))
    def test_failure_clears_envelope_parsed_values_and_vendor_context(self, _credentials: MagicMock) -> None:
        """Scrub all OAuth material before a safe typed failure escapes."""
        raw = json.dumps(OAUTH_FIELDS)

        try:
            build_google_credentials(
                raw,
                service_account_scopes=('scope',),
                subject=None,
                require_service_account_subject=False,
            )
        except GoogleCredentialError as failure:
            retained = _retained_google_auth_locals(failure)
            for sentinel in (
                raw,
                OAUTH_FIELDS['client_id'],
                OAUTH_FIELDS['client_secret'],
                OAUTH_FIELDS['refresh_token'],
                'provider-body-secret-sentinel',
            ):
                self.assertNotIn(str(sentinel), retained)
                self.assertNotIn(str(sentinel), str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('OAuth constructor failure did not raise GoogleCredentialError')
