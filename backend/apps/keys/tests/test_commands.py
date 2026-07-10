# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import SystemCredential, UserCredential
from apps.keys.services import commands
from django.contrib.auth import get_user_model

from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialCommands(OTransactionTestCase):
    def test_upsert_named_encrypts_and_lists_metadata(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user', password='x')
        meta = commands.upsert_user_named(user.pk, 'openai-work', 'openai', 'sk-user-key')
        self.assertTrue(meta.is_set)
        self.assertEqual(meta.name, 'openai-work')
        self.assertEqual(meta.scope, 'user')
        self.assertEqual(meta.source, 'db')
        self.assertEqual(meta.status, 'active')
        row = UserCredential.objects.get(user_id=user.pk, name='openai-work')
        self.assertNotEqual(row.encrypted_value, b'sk-user-key')

    def test_upsert_named_restores_database_provenance(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user-provenance', password='x')
        UserCredential.objects.create(
            user=user,
            name='openai-work',
            type='openai',
            encrypted_value=b'old',
            source='disk',
            source_path='keys/openai-work.yaml',
            source_rev='sha256:old',
            status='disabled',
        )

        commands.upsert_user_named(user.pk, 'openai-work', 'openai', 'sk-user-key')

        row = UserCredential.objects.get(user_id=user.pk, name='openai-work')
        self.assertEqual(row.source, 'db')
        self.assertEqual(row.source_path, '')
        self.assertEqual(row.source_rev, '')
        self.assertEqual(row.status, 'active')

    def test_upsert_named_rejects_reserved_prefix(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user2', password='x')
        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'default:evil', 'gmail', 'token')

    def test_delete_user_credential_idempotent_missing(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user3', password='x')
        with self.assertRaises(KeyNotFoundError):
            commands.delete_user_credential(user.pk, 'missing')

    def test_user_name_cannot_collide_with_system_namespace(self) -> None:
        SystemCredential.objects.create(
            name='shared-name',
            type='clickup',
            is_default=False,
            encrypted_value=b'x',
        )
        user = get_user_model().objects.create_user(username='cmd-user4', password='x')
        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'shared-name', 'clickup', 'tok')

    def test_set_system_default_clear_removes_row(self) -> None:
        meta = commands.set_system_default('openai', 'sk-system')
        self.assertTrue(meta.is_set)
        cleared = commands.set_system_default('openai', '')
        self.assertFalse(cleared.is_set)
        self.assertFalse(SystemCredential.objects.filter(type='openai', is_default=True).exists())

    def test_delete_system_credential(self) -> None:
        commands.set_system_default('anthropic', 'sk-a')
        commands.delete_system_credential('default:anthropic')
        self.assertFalse(SystemCredential.objects.filter(name='default:anthropic').exists())
