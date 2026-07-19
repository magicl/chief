# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, TypeVar
from unittest.mock import patch

from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyStorageMisconfiguredError,
    KeyTypeMismatchError,
    KeyValidationError,
)
from apps.keys.models import CredentialAuthKind, SystemCredential, UserCredential
from apps.keys.oauth.providers.google import GOOGLE_OAUTH_PROVIDER
from apps.keys.services import commands, queries
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

FailureT = TypeVar('FailureT', bound=Exception)


def _retained_text(value: Any, *, seen: set[int] | None = None, depth: int = 0) -> str:
    """Render reachable resolver failure state to detect retained secret sentinels."""
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
                'apps.keys.services.queries',
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
    """Run an action and retain the expected resolver failure traceback."""
    try:
        action()
    except Exception as failure:
        if isinstance(failure, expected_type):
            return failure
        raise
    raise AssertionError('Expected credential resolution to fail')


class TestCredentialQueries(OTransactionTestCase):
    def setUp(self) -> None:
        """Suppress resource transport while testing credential query behavior."""
        super().setUp()
        publisher = patch('apps.bus.resources.publish_resource_update')
        publisher.start()
        self.addCleanup(publisher.stop)

    def test_resolve_default_falls_back_to_system_then_env(self) -> None:
        user = get_user_model().objects.create_user(username='q-user2', password='x')
        commands.set_system_default('openai', 'sk-system')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env'}, clear=False):
            self.assertEqual(queries.resolve_default_secret(user.pk, 'openai'), 'sk-system')

    def test_resolve_default_uses_is_default_flag_not_name(self) -> None:
        from apps.keys import crypto

        user = get_user_model().objects.create_user(username='q-user-default-flag', password='x')
        SystemCredential.objects.create(
            name='platform-openai',
            type='openai',
            is_default=True,
            encrypted_value=crypto.encrypt('sk-platform'),
        )
        self.assertEqual(queries.resolve_default_secret(user.pk, 'openai'), 'sk-platform')

    def test_resolve_default_env_when_no_system_row(self) -> None:
        user = get_user_model().objects.create_user(username='q-user-env', password='x')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env'}, clear=False):
            self.assertEqual(queries.resolve_default_secret(user.pk, 'openai'), 'sk-env')

    def test_resolve_secret_by_system_name(self) -> None:
        user = get_user_model().objects.create_user(username='q-user3', password='x')
        commands.set_system_default('openai', 'sk-system')
        self.assertEqual(
            queries.resolve_secret(user.pk, 'default:openai', expected_type='openai'),
            'sk-system',
        )

    def test_resolve_skips_disabled_user_credential(self) -> None:
        user = get_user_model().objects.create_user(username='q-user-disabled', password='x')
        row = UserCredential.objects.create(
            user=user,
            name='openai-disabled',
            type='openai',
            encrypted_value=b'not-used',
            status='disabled',
        )

        with self.assertRaises(KeyNotFoundError):
            queries.resolve_secret(user.pk, row.name, expected_type='openai')

    def test_resolve_rejects_needs_attention_static_row_despite_ciphertext(self) -> None:
        """Block resolution of an active row flagged needs_attention even with a set secret."""
        user = get_user_model().objects.create_user(username='q-needs-attention', password='x')
        row = UserCredential.objects.create(
            user=user,
            name='disk-broken',
            type='openai',
            encrypted_value=b'stale-ciphertext-not-decryptable-as-fernet',
            source='disk',
            source_path='keys/disk-broken.yaml',
            health_status='needs_attention',
            health_code='invalid_declaration',
        )

        with self.assertRaisesRegex(KeyNotFoundError, rf'^credential not set: {row.name}$'):
            queries.resolve_secret(user.pk, row.name, expected_type='openai')

    def test_resolve_reports_not_connected_for_oauth_not_connected_health(self) -> None:
        """Surface the OAuth-specific message when health_code is oauth_not_connected."""
        user = get_user_model().objects.create_user(username='q-health-oauth', password='x')
        commands.upsert_user_named_from_disk(
            user.pk,
            'work-google',
            'google',
            None,
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
            source_path='keys/work-google.yaml',
            source_rev='sha256:first',
        )

        with self.assertRaisesRegex(KeyNotFoundError, r'^credential not connected: work-google$'):
            queries.resolve_secret(user.pk, 'work-google', expected_type='google')

    def test_type_mismatch_raises(self) -> None:
        user = get_user_model().objects.create_user(username='q-user4', password='x')
        commands.upsert_user_named(user.pk, 'my-clickup', 'clickup', 'tok')
        with self.assertRaises(KeyTypeMismatchError):
            queries.resolve_secret(user.pk, 'my-clickup', expected_type='google')

    def test_list_metadata_never_includes_plaintext(self) -> None:
        user = get_user_model().objects.create_user(username='q-user5', password='x')
        commands.upsert_user_named(user.pk, 'openai-work', 'openai', 'sk-hidden')
        metas = queries.list_user_credentials(user.pk)
        payload = str(metas)
        self.assertNotIn('sk-hidden', payload)

    def test_oauth_metadata_exposes_valid_provider_configuration(self) -> None:
        """OAuth metadata includes only the non-secret provider and capability identifiers."""
        user = get_user_model().objects.create_user(username='q-oauth-metadata', password='x')
        UserCredential.objects.create(
            user=user,
            name='google-oauth',
            type='google',
            auth_kind='oauth',
            auth_config={
                'provider': 'google',
                'capabilities': ['gmail_read', 'drive_metadata'],
            },
            encrypted_value=b'opaque-grant',
        )

        with patch('apps.keys.services.queries.crypto.decrypt') as decrypt:
            metadata = queries.list_user_credentials(user.pk)[0]

        self.assertEqual(metadata.auth_kind, 'oauth')
        self.assertEqual(metadata.oauth_provider, 'google')
        self.assertEqual(metadata.oauth_capabilities, ('gmail_read', 'drive_metadata'))
        decrypt.assert_not_called()
        self.assertNotIn('opaque-grant', repr(metadata))

    def test_malformed_auth_config_produces_empty_oauth_metadata(self) -> None:
        """Noncanonical stored OAuth metadata is ignored without decrypting credential material."""
        user = get_user_model().objects.create_user(username='q-malformed-metadata', password='x')
        malformed_rows: tuple[tuple[str, object], ...] = (
            ('google', []),
            ('google', {'provider': 'google', 'capabilities': 'gmail_read'}),
            ('google', {'provider': '', 'capabilities': ['gmail_read']}),
            ('google', {'provider': 'google', 'capabilities': ['gmail_read', 3]}),
            ('google', {'provider': 'unknown-provider', 'capabilities': ['gmail_read']}),
            ('google', {'provider': 'google', 'capabilities': ['unknown-capability']}),
            ('clickup', {'provider': 'google', 'capabilities': ['gmail_read']}),
            ('google', {'provider': 'google', 'capabilities': ['gmail_read', 'gmail_read']}),
            ('google', {'provider': 'google', 'capabilities': ['drive_metadata', 'gmail_read']}),
        )

        with patch('apps.keys.services.queries.crypto.decrypt') as decrypt:
            for index, (credential_type, auth_config) in enumerate(malformed_rows):
                row = UserCredential.objects.create(
                    user=user,
                    name=f'malformed-{index}',
                    type=credential_type,
                    auth_kind='oauth',
                    auth_config=auth_config,
                    encrypted_value=b'opaque-grant',
                )
                metadata = next(item for item in queries.list_user_credentials(user.pk) if item.name == row.name)
                self.assertIsNone(metadata.oauth_provider)
                self.assertEqual(metadata.oauth_capabilities, ())

        decrypt.assert_not_called()

    def test_static_metadata_ignores_oauth_shaped_configuration(self) -> None:
        """Static rows never advertise OAuth metadata even if stored JSON has that shape."""
        user = get_user_model().objects.create_user(username='q-static-metadata', password='x')
        UserCredential.objects.create(
            user=user,
            name='google-static',
            type='google',
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
            encrypted_value=b'opaque-static-secret',
        )

        metadata = queries.list_user_credentials(user.pk)[0]

        self.assertEqual(metadata.auth_kind, 'static')
        self.assertIsNone(metadata.oauth_provider)
        self.assertEqual(metadata.oauth_capabilities, ())

    def test_resolve_unconnected_oauth_raises_typed_missing_credential(self) -> None:
        """Treat an OAuth declaration without a grant as an unconnected credential."""
        user = get_user_model().objects.create_user(username='q-oauth-unconnected', password='x')
        commands.create_user_oauth(
            user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=('gmail_read',),
        )

        with self.assertRaisesRegex(KeyNotFoundError, r'^credential not connected: google-oauth$'):
            queries.resolve_secret(user.pk, 'google-oauth', expected_type='google')

    def test_resolve_connected_oauth_materializes_only_at_call_time(self) -> None:
        """Decrypt and materialize a connected OAuth grant during immediate resolution."""
        from apps.keys import crypto

        user = get_user_model().objects.create_user(username='q-oauth-connected', password='x')
        grant = 'stored-oauth-grant-secret-sentinel'
        runtime = json.dumps(
            {
                'chief_google_oauth': 1,
                'client_id': 'runtime-client-id',
                'client_secret': 'runtime-client-secret',
                'refresh_token': 'runtime-refresh-token',
                'scopes': ['https://www.googleapis.com/auth/gmail.readonly'],
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        )
        row = UserCredential.objects.create(
            user=user,
            name='google-oauth',
            type='google',
            encrypted_value=crypto.encrypt(grant),
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
        )

        with patch.object(GOOGLE_OAUTH_PROVIDER, 'materialize_runtime', return_value=runtime) as materialize:
            supplier = queries.make_secret_supplier(user.pk, name=row.name, type='google')
            materialize.assert_not_called()
            resolved = supplier()
            self.assertIsInstance(resolved, str)
            assert resolved is not None
            self.assertEqual(resolved, runtime)
            self.assertEqual(
                set(json.loads(resolved)),
                {
                    'chief_google_oauth',
                    'client_id',
                    'client_secret',
                    'refresh_token',
                    'scopes',
                    'token_uri',
                },
            )
            self.assertNotIn('access_token', resolved)

        materialize.assert_called_once_with(
            grant_payload=grant,
            capability_ids=('gmail_read',),
        )

    def test_resolve_oauth_rejects_malformed_config_before_decrypting(self) -> None:
        """Reject invalid stored declarations without exposing or decrypting the grant."""
        user = get_user_model().objects.create_user(username='q-oauth-malformed', password='x')
        UserCredential.objects.create(
            user=user,
            name='google-oauth',
            type='google',
            encrypted_value=b'opaque-oauth-grant',
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': []},
        )

        with patch('apps.keys.services.queries.crypto.decrypt') as decrypt:
            with self.assertRaises(KeyValidationError):
                queries.resolve_secret(user.pk, 'google-oauth', expected_type='google')
        decrypt.assert_not_called()

    def test_resolve_oauth_scrubs_grant_and_provider_context_on_failure(self) -> None:
        """Prevent decrypted grants and provider detail from surviving in traceback state."""
        from apps.keys import crypto

        user = get_user_model().objects.create_user(username='q-oauth-scrubbed', password='x')
        grant = 'resolver-grant-secret-sentinel'
        provider_detail = 'resolver-provider-detail-secret-sentinel'
        UserCredential.objects.create(
            user=user,
            name='google-oauth',
            type='google',
            encrypted_value=crypto.encrypt(grant),
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
        )

        with patch.object(
            GOOGLE_OAUTH_PROVIDER,
            'materialize_runtime',
            side_effect=RuntimeError(provider_detail),
        ):
            failure = _capture_failure(
                lambda: queries.resolve_secret(user.pk, 'google-oauth', expected_type='google'),
                KeyValidationError,
            )

        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__cause__)
        retained = _retained_text(failure)
        self.assertNotIn(grant, retained)
        self.assertNotIn(provider_detail, retained)

    def test_no_cross_user_leakage(self) -> None:
        u1 = get_user_model().objects.create_user(username='q-u1', password='x')
        u2 = get_user_model().objects.create_user(username='q-u2', password='x')
        commands.upsert_user_named(u1.pk, 'private', 'google', 'tok1')
        with self.assertRaises(KeyNotFoundError):
            queries.resolve_secret(u2.pk, 'private', expected_type='google')

    @expectLogItems([ExpectLogItem('apps.keys.crypto', logging.WARNING, r'credential decrypt failed', count=1)])
    def test_resolve_raises_when_master_key_rotated(self) -> None:
        user = get_user_model().objects.create_user(username='q-decrypt', password='x')
        key_one = Fernet.generate_key().decode()
        key_two = Fernet.generate_key().decode()
        with override_settings(CREDENTIALS_KEY=key_one):
            commands.set_system_default('openai', 'sk-stored')
        with override_settings(CREDENTIALS_KEY=key_two):
            with self.assertRaises(KeyStorageMisconfiguredError):
                queries.resolve_default_secret(user.pk, 'openai')

    def test_list_referenceable_credentials_merges_scopes(self) -> None:
        user = get_user_model().objects.create_user(username='q-ref', password='x')
        commands.set_system_default('openai', 'sk-sys')
        commands.upsert_user_named(user.pk, 'gmail-personal', 'google', 'tok')
        refs = queries.list_referenceable_credentials(user.pk, type='google')
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, 'gmail-personal')
        all_refs = queries.list_referenceable_credentials(user.pk)
        names = {meta.name for meta in all_refs}
        self.assertIn('default:openai', names)
        self.assertIn('gmail-personal', names)
