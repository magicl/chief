# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Security and lifecycle tests for OAuth credential services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast
from unittest.mock import MagicMock, patch

from apps.keys import crypto
from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyStorageMisconfiguredError,
    KeyValidationError,
    OAuthConfigurationError,
    OAuthProviderError,
    OAuthStateError,
)
from apps.keys.models import CredentialAuthKind, CredentialStatus, UserCredential
from apps.keys.oauth.providers.google import GOOGLE_OAUTH_PROVIDER
from apps.keys.oauth.services import (
    STATE_SALT,
    OAuthStart,
    auth_config_fingerprint,
    complete_authorization,
    disconnect_authorization,
    normalize_auth_config,
    start_authorization,
)
from apps.keys.services import commands
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import override_settings

from olib.py.django.test.cases import OTestCase

CLIENT_ID = 'service-client-id-secret-sentinel'
CLIENT_SECRET = 'service-client-secret-sentinel'
AUTHORIZATION_CODE = 'service-authorization-code-secret-sentinel'
OLD_GRANT = 'service-old-grant-secret-sentinel'
NEW_GRANT = 'service-new-grant-secret-sentinel'
STALE_GRANT = 'service-stale-grant-secret-sentinel'
FailureT = TypeVar('FailureT', bound=Exception)


class _FailingEncryptor:
    """Raise a plaintext-bearing failure from the real crypto.encrypt frame."""

    def encrypt(self, plaintext: bytes) -> bytes:
        """Fail after receiving plaintext so traceback scrubbing is observable."""
        raise RuntimeError(f'encryption-provider-detail-secret-sentinel:{plaintext!r}')


def _retained_text(value: Any, *, seen: set[int] | None = None, depth: int = 0) -> str:
    """Render reachable OAuth service state to detect retained callback secrets."""
    if seen is None:
        seen = set()
    if depth > 8 or id(value) in seen:
        return ''
    seen.add(id(value))
    rendered: list[str] = []
    for renderer in (str, repr):
        try:
            rendered.append(renderer(value))
        except Exception:  # pragma: no cover  # pylint: disable=broad-exception-caught
            rendered.append('<unrenderable>')
    if isinstance(value, BaseException):
        rendered.extend(_retained_text(item, seen=seen, depth=depth + 1) for item in value.args)
        if value.__context__ is not None:
            rendered.append(_retained_text(value.__context__, seen=seen, depth=depth + 1))
        if value.__cause__ is not None:
            rendered.append(_retained_text(value.__cause__, seen=seen, depth=depth + 1))
        traceback = value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_globals.get('__name__') in {
                'apps.keys.crypto',
                'apps.keys.oauth.services',
            }:
                rendered.append(_retained_text(traceback.tb_frame.f_locals, seen=seen, depth=depth + 1))
            traceback = traceback.tb_next
    elif isinstance(value, Mapping):
        for key, item in value.items():
            rendered.append(_retained_text(key, seen=seen, depth=depth + 1))
            rendered.append(_retained_text(item, seen=seen, depth=depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        rendered.extend(_retained_text(item, seen=seen, depth=depth + 1) for item in value)
    elif hasattr(value, '__dict__'):
        rendered.append(_retained_text(vars(value), seen=seen, depth=depth + 1))
    return '\n'.join(rendered)


def _capture_failure(action: Callable[[], Any], expected_type: type[FailureT]) -> FailureT:
    """Run an action and preserve an expected OAuth service failure traceback."""
    try:
        action()
    except Exception as failure:
        if isinstance(failure, expected_type):
            return failure
        raise
    raise AssertionError('Expected OAuth service operation to fail')


@override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET=CLIENT_SECRET)
class TestOAuthServices(OTestCase):
    """Exercise declaration and authorization transitions at the app boundary."""

    def setUp(self) -> None:
        """Create two owners and isolate one-time state markers per test."""
        super().setUp()
        self.oauth_markers: set[str] = set()
        self.user = get_user_model().objects.create_user(username='oauth-service-user')
        self.other_user = get_user_model().objects.create_user(username='oauth-service-other')

    def tearDown(self) -> None:
        """Remove only OAuth markers created by this test."""
        cache.delete_many(self.oauth_markers)
        super().tearDown()

    def _create_oauth(self, *, capabilities: tuple[str, ...] = ('gmail_read',)) -> UserCredential:
        """Create an unconnected OAuth declaration for the primary owner."""
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=capabilities,
        )
        return UserCredential.objects.get(user=self.user, name='google-oauth')

    def _start(self, row: UserCredential, *, session_key: str = 'session-one') -> OAuthStart:
        """Start a flow while replacing the external URL builder."""
        with patch.object(
            GOOGLE_OAUTH_PROVIDER,
            'build_authorization_url',
            return_value='https://accounts.example.test/authorize',
        ) as build:
            started = start_authorization(
                user_id=self.user.pk,
                credential_id=row.pk,
                session_key=session_key,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )
        build.assert_called_once()
        payload = self._state_payload(started.state)
        nonce = cast(str, payload['nonce'])
        self.oauth_markers.add(f'keys:oauth-state:{hashlib.sha256(nonce.encode()).hexdigest()}')
        return started

    def _state_payload(self, state: str) -> dict[str, object]:
        """Decode signed state in tests without weakening production validation."""
        payload = signing.loads(state, salt=STATE_SALT)
        self.assertIsInstance(payload, dict)
        return cast(dict[str, object], payload)

    def _resign(self, payload: Mapping[str, object]) -> str:
        """Sign a controlled state payload for binding-failure tests."""
        return signing.dumps(dict(payload), salt=STATE_SALT)

    def test_normalize_auth_config_returns_exact_provider_shape(self) -> None:
        """Normalize duplicate capabilities into provider catalog order."""
        normalized = normalize_auth_config(
            provider_id='google',
            credential_type='google',
            capability_ids=('drive_metadata', 'gmail_read', 'drive_metadata'),
        )

        self.assertEqual(
            normalized,
            {'provider': 'google', 'capabilities': ['gmail_read', 'drive_metadata']},
        )

    def test_normalize_auth_config_rejects_provider_type_mismatch(self) -> None:
        """Reject declarations whose provider does not own the credential type."""
        with self.assertRaises(KeyValidationError):
            normalize_auth_config(
                provider_id='google',
                credential_type='openai',
                capability_ids=('gmail_read',),
            )

    def test_auth_config_fingerprint_is_canonical_compact_sha256(self) -> None:
        """Fingerprint equivalent mappings using canonical compact JSON."""
        config = {'provider': 'google', 'capabilities': ['gmail_read']}
        expected = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

        self.assertEqual(auth_config_fingerprint(config), expected)
        self.assertEqual(
            auth_config_fingerprint({'capabilities': ['gmail_read'], 'provider': 'google'}),
            expected,
        )

    def test_create_user_oauth_persists_unconnected_normalized_declaration(self) -> None:
        """Create an active OAuth row with no encrypted grant."""
        metadata = commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=('drive_metadata', 'gmail_read'),
        )
        row = UserCredential.objects.get(user=self.user, name='google-oauth')

        self.assertEqual(row.auth_kind, CredentialAuthKind.OAUTH)
        self.assertEqual(
            row.auth_config,
            {'provider': 'google', 'capabilities': ['gmail_read', 'drive_metadata']},
        )
        self.assertEqual(row.encrypted_value, b'')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertFalse(metadata.is_set)

    def test_create_user_oauth_rejects_non_google_and_empty_selection(self) -> None:
        """Allow only the registered Google type and a non-empty capability set."""
        cases = (
            ('openai', 'google', ('gmail_read',)),
            ('google', 'missing-provider', ('gmail_read',)),
            ('google', 'google', ()),
        )
        for type_name, provider_id, capabilities in cases:
            with self.subTest(type_name=type_name, provider_id=provider_id):
                with self.assertRaises(KeyValidationError):
                    commands.create_user_oauth(
                        self.user.pk,
                        'google-oauth',
                        type_name,
                        provider_id=provider_id,
                        capability_ids=capabilities,
                    )

    def test_start_rejects_ineligible_rows_without_decrypting(self) -> None:
        """Reject ownership, status, auth-kind, and config failures before decryption."""
        oauth_row = self._create_oauth()
        static_row = UserCredential.objects.create(
            user=self.user,
            name='google-static',
            type='google',
            encrypted_value=b'opaque-static',
        )
        malformed_row = UserCredential.objects.create(
            user=self.user,
            name='google-malformed',
            type='google',
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={},
        )
        disabled_row = UserCredential.objects.create(
            user=self.user,
            name='google-disabled',
            type='google',
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
            status=CredentialStatus.DISABLED,
        )

        with patch('apps.keys.oauth.services.crypto.decrypt') as decrypt:
            cases = (
                (self.other_user.pk, oauth_row.pk),
                (self.user.pk, static_row.pk),
                (self.user.pk, malformed_row.pk),
                (self.user.pk, disabled_row.pk),
            )
            for user_id, credential_id in cases:
                with self.subTest(user_id=user_id, credential_id=credential_id):
                    with self.assertRaises((KeyNotFoundError, KeyValidationError)):
                        start_authorization(
                            user_id=user_id,
                            credential_id=credential_id,
                            session_key='session-one',
                            redirect_uri='https://chief.example.test/oauth/google/callback',
                        )
        decrypt.assert_not_called()

    def test_start_signs_secret_free_bound_state_and_adds_digest_marker(self) -> None:
        """Bind signed state to owner, row, provider, session, config, and random nonce."""
        row = self._create_oauth(capabilities=('drive_metadata', 'gmail_read'))
        started = self._start(row)
        payload = self._state_payload(started.state)

        self.assertEqual(payload['user_id'], self.user.pk)
        self.assertEqual(payload['credential_id'], str(row.pk))
        self.assertEqual(payload['provider'], 'google')
        self.assertEqual(
            payload['session_binding'],
            hashlib.sha256(b'session-one').hexdigest(),
        )
        self.assertEqual(payload['config_fingerprint'], auth_config_fingerprint(row.auth_config))
        self.assertEqual(
            payload['grant_fingerprint'],
            hashlib.sha256(bytes(row.encrypted_value)).hexdigest(),
        )
        nonce = payload['nonce']
        self.assertIsInstance(nonce, str)
        nonce_value = cast(str, nonce)
        self.assertGreaterEqual(len(nonce_value), 40)
        marker = f'keys:oauth-state:{hashlib.sha256(nonce_value.encode()).hexdigest()}'
        self.assertTrue(cache.get(marker))
        rendered = f'{payload!s} {payload!r} {started!s} {started!r}'
        for secret in (CLIENT_SECRET, OLD_GRANT, NEW_GRANT, AUTHORIZATION_CODE):
            self.assertNotIn(secret, rendered)

    def test_start_requires_cache_add_to_claim_unique_nonce(self) -> None:
        """Fail safely if a generated nonce cannot acquire its one-time marker."""
        row = self._create_oauth()

        with (
            patch('apps.keys.oauth.services.cache.add', return_value=False),
            patch.object(GOOGLE_OAUTH_PROVIDER, 'build_authorization_url') as build,
            self.assertRaisesRegex(OAuthStateError, r'^OAuth authorization could not be started$'),
        ):
            start_authorization(
                user_id=self.user.pk,
                credential_id=row.pk,
                session_key='session-one',
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        build.assert_not_called()

    def test_start_configuration_failure_scrubs_signed_state_and_context(self) -> None:
        """Discard generated state when provider configuration prevents a redirect."""
        row = self._create_oauth()
        state_sentinel = 'service-signed-state-secret-sentinel'
        with (
            patch('apps.keys.oauth.services.signing.dumps', return_value=state_sentinel),
            patch.object(
                GOOGLE_OAUTH_PROVIDER,
                'build_authorization_url',
                side_effect=OAuthConfigurationError('safe configuration failure'),
            ),
        ):
            failure = _capture_failure(
                lambda: start_authorization(
                    user_id=self.user.pk,
                    credential_id=row.pk,
                    session_key='session-one',
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                ),
                OAuthConfigurationError,
            )

        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__cause__)
        self.assertNotIn(state_sentinel, _retained_text(failure))

    def test_complete_rejects_tampered_expired_and_replayed_state_before_exchange(self) -> None:
        """Reject invalid or consumed signed state without contacting the provider."""
        row = self._create_oauth()
        started = self._start(row)

        with patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code') as exchange:
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state + 'tampered',
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )
            with override_settings(OAUTH_STATE_MAX_AGE_SECONDS=-1), self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )

            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=None,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )
        exchange.assert_not_called()

    def test_complete_rejects_user_session_provider_and_config_mismatch_before_exchange(self) -> None:
        """Validate every signed binding before consuming state or exchanging a code."""
        row = self._create_oauth()
        started = self._start(row)
        payload = self._state_payload(started.state)

        with patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code') as exchange:
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.other_user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='other-session',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )

            provider_payload = dict(payload, provider='other-provider')
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=self._resign(provider_payload),
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )

            UserCredential.objects.filter(pk=row.pk).update(
                auth_config={'provider': 'google', 'capabilities': ['gmail_send']}
            )
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )
        exchange.assert_not_called()

    def test_complete_binding_failure_scrubs_state_code_and_context(self) -> None:
        """Remove callback state and code from service frames on pre-exchange rejection."""
        row = self._create_oauth()
        started = self._start(row)

        failure = _capture_failure(
            lambda: complete_authorization(
                user_id=self.user.pk,
                session_key='other-session',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            ),
            OAuthStateError,
        )

        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__cause__)
        retained = _retained_text(failure)
        self.assertNotIn(started.state, retained)
        self.assertNotIn(AUTHORIZATION_CODE, retained)

    def test_consent_denial_consumes_state_without_exchange_or_grant_change(self) -> None:
        """Consume denied consent exactly once while preserving an existing grant."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        started = self._start(row)

        with patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code') as exchange:
            metadata = complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=None,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertTrue(metadata.is_set)
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), OLD_GRANT)
        exchange.assert_not_called()

    def test_blank_code_consumes_state_and_preserves_old_grant(self) -> None:
        """Reject a callback without a usable code after consuming its one-time state."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        old_ciphertext = bytes(row.encrypted_value)
        started = self._start(row)

        with patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code') as exchange:
            failure = _capture_failure(
                lambda: complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code='',
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                ),
                OAuthStateError,
            )
            with self.assertRaises(OAuthStateError):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), old_ciphertext)
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), OLD_GRANT)
        self.assertNotIn(started.state, _retained_text(failure))
        self.assertNotIn(AUTHORIZATION_CODE, _retained_text(failure))
        exchange.assert_not_called()

    def test_provider_failure_preserves_old_ciphertext(self) -> None:
        """Leave the current grant byte-for-byte unchanged when exchange fails."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        old_ciphertext = bytes(row.encrypted_value)
        started = self._start(row)

        with (
            patch.object(
                GOOGLE_OAUTH_PROVIDER,
                'exchange_code',
                side_effect=OAuthProviderError('safe provider failure'),
            ),
            self.assertRaises(OAuthProviderError),
        ):
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), old_ciphertext)

    @patch('apps.keys.oauth.services.publish_resource_update_after_commit')
    @patch('apps.keys.oauth.providers.google.httpx.post')
    def test_partial_scope_rejection_preserves_grant_consumes_state_and_skips_refresh(
        self,
        post: MagicMock,
        publish_after_commit: MagicMock,
    ) -> None:
        """Reject an incomplete provider grant without writing or allowing replay."""
        row = self._create_oauth(capabilities=('gmail_read', 'gmail_send'))
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        old_ciphertext = bytes(row.encrypted_value)
        started = self._start(row)
        partial_refresh_token = 'partial-refresh-token-secret-sentinel'
        access_token = 'partial-access-token-secret-sentinel'
        provider_detail = 'partial-provider-detail-secret-sentinel'
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'access_token': access_token,
            'refresh_token': partial_refresh_token,
            'scope': 'https://www.googleapis.com/auth/gmail.readonly',
            'provider_extra': provider_detail,
        }
        post.return_value = response

        with self.assertRaisesRegex(
            OAuthProviderError,
            r'^Google OAuth code exchange failed$',
        ) as caught:
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), old_ciphertext)
        publish_after_commit.assert_not_called()
        rendered = f'{caught.exception!s} {caught.exception!r}'
        for secret in (AUTHORIZATION_CODE, partial_refresh_token, access_token, provider_detail):
            self.assertNotIn(secret, rendered)

        with self.assertRaises(OAuthStateError):
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )
        post.assert_called_once()
        publish_after_commit.assert_not_called()

    def test_concurrent_declaration_change_after_exchange_preserves_ciphertext(self) -> None:
        """Reject a row changed during provider exchange before grant replacement."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        old_ciphertext = bytes(row.encrypted_value)
        started = self._start(row)

        def exchange_then_change(**_: object) -> str:
            """Simulate a declaration update while the provider call is in flight."""
            UserCredential.objects.filter(pk=row.pk).update(
                auth_config={'provider': 'google', 'capabilities': ['gmail_send']}
            )
            return NEW_GRANT

        with (
            patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code', side_effect=exchange_then_change),
            self.assertRaises(OAuthStateError),
        ):
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), old_ciphertext)

    @patch('apps.keys.oauth.services.publish_resource_update_after_commit')
    def test_overlapping_reauthentication_rejects_stale_locked_baseline(
        self,
        publish_after_commit: MagicMock,
    ) -> None:
        """Prevent an older in-flight callback from replacing a newer completed grant."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        older = self._start(row)
        newer = self._start(row)

        def complete_newer_during_exchange(**_: object) -> str:
            """Complete the newer flow while the older flow is exchanging its code."""
            with patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code', return_value=NEW_GRANT):
                complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=newer.state,
                    code='newer-authorization-code-secret-sentinel',
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                )
            publish_after_commit.reset_mock()
            return STALE_GRANT

        with (
            patch.object(
                GOOGLE_OAUTH_PROVIDER,
                'exchange_code',
                side_effect=complete_newer_during_exchange,
            ),
            self.assertRaises(OAuthStateError),
        ):
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=older.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), NEW_GRANT)
        publish_after_commit.assert_not_called()

    @patch('apps.keys.oauth.services.publish_resource_update_after_commit')
    def test_disconnect_then_callback_rejects_stale_baseline_without_exchange(
        self,
        publish_after_commit: MagicMock,
    ) -> None:
        """Prevent a callback started before disconnect from reconnecting the row."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        started = self._start(row)
        disconnect_authorization(user_id=self.user.pk, credential_id=row.pk)
        publish_after_commit.reset_mock()

        with (
            patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code', return_value=STALE_GRANT) as exchange,
            self.assertRaises(OAuthStateError),
        ):
            complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), b'')
        exchange.assert_not_called()
        publish_after_commit.assert_not_called()

    @patch('apps.keys.oauth.services.publish_resource_update_after_commit')
    def test_encryption_failure_scrubs_plaintext_and_preserves_row(
        self,
        publish_after_commit: MagicMock,
    ) -> None:
        """Sanitize encryption failures after clearing all plaintext-bearing frames."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        old_ciphertext = bytes(row.encrypted_value)
        started = self._start(row)

        with (
            patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code', return_value=NEW_GRANT),
            patch('apps.keys.crypto._fernet', return_value=_FailingEncryptor()),
        ):
            failure = _capture_failure(
                lambda: complete_authorization(
                    user_id=self.user.pk,
                    session_key='session-one',
                    state=started.state,
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/google/callback',
                ),
                KeyStorageMisconfiguredError,
            )

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), old_ciphertext)
        publish_after_commit.assert_not_called()
        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__cause__)
        retained = _retained_text(failure)
        self.assertNotIn(NEW_GRANT, retained)
        self.assertNotIn('encryption-provider-detail-secret-sentinel', retained)

    @patch('apps.bus.resources.publish_resource_update')
    def test_success_replaces_grant_and_publishes_once_after_commit(self, publish: MagicMock) -> None:
        """Atomically replace a grant and emit one committed keys refresh."""
        row = self._create_oauth()
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.save(update_fields=['encrypted_value'])
        started = self._start(row)
        publish.reset_mock()

        with (
            patch.object(GOOGLE_OAUTH_PROVIDER, 'exchange_code', return_value=NEW_GRANT),
            patch('apps.keys.oauth.services.UserCredential.objects.select_for_update') as lock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            lock.return_value.get.return_value = row
            metadata = complete_authorization(
                user_id=self.user.pk,
                session_key='session-one',
                state=started.state,
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/google/callback',
            )

        row.refresh_from_db()
        self.assertTrue(metadata.is_set)
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), NEW_GRANT)
        lock.assert_called_once_with()
        publish.assert_called_once_with(self.user.pk, 'keys')

    @patch('apps.bus.resources.publish_resource_update')
    def test_disconnect_clears_only_grant_and_publishes(self, publish: MagicMock) -> None:
        """Disconnect while retaining declaration identity and provenance."""
        row = self._create_oauth(capabilities=('gmail_read', 'drive_metadata'))
        row.encrypted_value = crypto.encrypt(OLD_GRANT)
        row.source = 'disk'
        row.source_path = 'keys/google-oauth.yaml'
        row.source_rev = 'sha256:declaration'
        row.save(update_fields=['encrypted_value', 'source', 'source_path', 'source_rev'])
        expected = {
            'name': row.name,
            'type': row.type,
            'source': row.source,
            'source_path': row.source_path,
            'source_rev': row.source_rev,
            'status': row.status,
            'auth_kind': row.auth_kind,
            'auth_config': row.auth_config,
        }
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            metadata = disconnect_authorization(user_id=self.user.pk, credential_id=row.pk)

        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertFalse(metadata.is_set)
        for field, value in expected.items():
            self.assertEqual(getattr(row, field), value)
        publish.assert_called_once_with(self.user.pk, 'keys')

    def test_disconnect_rejects_cross_user_static_and_disabled_rows(self) -> None:
        """Permit disconnect only for the owner's active OAuth declaration."""
        oauth_row = self._create_oauth()
        static_row = UserCredential.objects.create(
            user=self.user,
            name='google-static',
            type='google',
            encrypted_value=b'opaque-static',
        )
        oauth_row.status = CredentialStatus.DISABLED
        oauth_row.save(update_fields=['status'])

        for user_id, credential_id in (
            (self.other_user.pk, oauth_row.pk),
            (self.user.pk, oauth_row.pk),
            (self.user.pk, static_row.pk),
        ):
            with self.subTest(user_id=user_id, credential_id=credential_id):
                with self.assertRaises((KeyNotFoundError, KeyValidationError)):
                    disconnect_authorization(user_id=user_id, credential_id=credential_id)
