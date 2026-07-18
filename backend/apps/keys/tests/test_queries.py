# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging
import os
from unittest.mock import patch

from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyStorageMisconfiguredError,
    KeyTypeMismatchError,
)
from apps.keys.models import SystemCredential, UserCredential
from apps.keys.services import commands, queries
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


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
