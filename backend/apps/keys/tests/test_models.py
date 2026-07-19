# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys import models as key_models
from apps.keys.models import SystemCredential, UserCredential
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from olib.py.django.test.cases import OTransactionTestCase


class TestCredentialModels(OTransactionTestCase):
    def test_user_credential_provenance_defaults(self) -> None:
        """New user credentials default to static authentication and database provenance."""
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
        self.assertEqual(row.auth_kind, key_models.CredentialAuthKind.STATIC)
        self.assertEqual(row.auth_config, {})
        self.assertEqual(bytes(row.encrypted_value), b'x')

    def test_auth_kind_choices_are_stable(self) -> None:
        """Credential authentication choices retain their persisted string values."""
        self.assertEqual(key_models.CredentialAuthKind.STATIC, 'static')
        self.assertEqual(key_models.CredentialAuthKind.OAUTH, 'oauth')

    def test_user_credential_allows_an_empty_ciphertext(self) -> None:
        """OAuth declarations can exist before encrypted grant material is available."""
        field = UserCredential._meta.get_field('encrypted_value')

        self.assertTrue(field.blank)
        self.assertIs(field.default, bytes)

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

    def test_user_credential_health_defaults_ready(self) -> None:
        """New user credentials default to a ready health status with no code."""
        user = get_user_model().objects.create_user(username='health-defaults-user', password='x')
        row = UserCredential.objects.create(
            user=user,
            name='openai-health-defaults',
            type='openai',
            encrypted_value=b'x',
        )

        self.assertEqual(row.health_status, key_models.CredentialHealthStatus.READY)
        self.assertEqual(row.health_code, '')

    def test_health_status_choices_are_stable(self) -> None:
        """Credential health status choices retain their persisted string values."""
        self.assertEqual(key_models.CredentialHealthStatus.READY, 'ready')
        self.assertEqual(key_models.CredentialHealthStatus.NEEDS_ATTENTION, 'needs_attention')

    def test_user_name_unique_per_user(self) -> None:
        user = get_user_model().objects.create_user(username='u1', password='x')
        UserCredential.objects.create(
            user=user,
            name='gmail-personal',
            type='google',
            encrypted_value=b'x',
        )
        with self.assertRaises(IntegrityError):
            UserCredential.objects.create(
                user=user,
                name='gmail-personal',
                type='google',
                encrypted_value=b'y',
            )
