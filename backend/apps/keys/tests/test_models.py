# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.models import SystemCredential, UserCredential
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialModels(OTransactionTestCase):
    def test_user_credential_provenance_defaults(self) -> None:
        user = get_user_model().objects.create_user(username='defaults-user', password='x')
        row = UserCredential.objects.create(
            user=user,
            name='openai-defaults',
            type='openai',
            encrypted_value=b'x',
        )

        self.assertEqual(row.source, 'db')
        self.assertEqual(row.source_path, '')
        self.assertEqual(row.source_rev, '')
        self.assertEqual(row.status, 'active')

    def test_system_default_flag_is_unique_per_type(self) -> None:
        SystemCredential.objects.create(
            name='default:openai',
            type='openai',
            is_default=True,
            encrypted_value=b'ciphertext',
        )
        with self.assertRaises(IntegrityError):
            SystemCredential.objects.create(
                name='other-openai',
                type='openai',
                is_default=True,
                encrypted_value=b'other',
            )

    def test_user_name_unique_per_user(self) -> None:
        user = get_user_model().objects.create_user(username='u1', password='x')
        UserCredential.objects.create(
            user=user,
            name='gmail-personal',
            type='gmail',
            encrypted_value=b'x',
        )
        with self.assertRaises(IntegrityError):
            UserCredential.objects.create(
                user=user,
                name='gmail-personal',
                type='gmail',
                encrypted_value=b'y',
            )
