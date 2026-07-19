# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for keys data migrations."""

from importlib import import_module

from apps.keys.models import SystemCredential, UserCredential
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import migrations

from olib.py.django.test.cases import OTestCase


class TestCredentialAuthenticationMetadataMigration(OTestCase):
    """Verify the generated schema migration preserves existing credential material."""

    def test_generated_operations_add_defaults_without_data_rewrite(self) -> None:
        """The schema-only migration adds static metadata and permits empty ciphertext."""
        try:
            migration = import_module('apps.keys.migrations.0006_usercredential_auth_config_usercredential_auth_kind')
        except ModuleNotFoundError:
            self.fail('credential authentication metadata migration has not been generated')

        operations = migration.Migration.operations
        added_fields = {
            operation.name: operation.field for operation in operations if isinstance(operation, migrations.AddField)
        }
        altered_fields = {
            operation.name: operation.field for operation in operations if isinstance(operation, migrations.AlterField)
        }

        self.assertEqual(added_fields['auth_kind'].default, 'static')
        self.assertIs(added_fields['auth_config'].default, dict)
        self.assertFalse(any(isinstance(operation, migrations.RunPython) for operation in operations))
        self.assertTrue(altered_fields['encrypted_value'].blank)
        self.assertIs(altered_fields['encrypted_value'].default, bytes)


class TestRenameGmailCredentialsToGoogle(OTestCase):
    """Verify the credential metadata cutover preserves encrypted values."""

    def setUp(self) -> None:
        """Load the generated migration function under test."""
        super().setUp()
        migration = import_module('apps.keys.migrations.0005_rename_gmail_credentials_to_google')
        self.rename = migration.rename_gmail_credentials_to_google

    def _seed_nondefault_gmail_rows(self) -> tuple[UserCredential, SystemCredential]:
        """Create representative non-default rows that preflight failures must not mutate."""
        user = get_user_model().objects.create_user(username='migration-conflict-user')
        user_row = UserCredential.objects.create(
            user=user,
            name='gmail-personal',
            type='gmail',
            encrypted_value=b'user-conflict-ciphertext',
        )
        system_row = SystemCredential.objects.create(
            name='gmail-secondary',
            type='gmail',
            is_default=False,
            encrypted_value=b'system-conflict-ciphertext',
        )
        return user_row, system_row

    def _assert_nondefault_gmail_rows_unchanged(
        self,
        user_row: UserCredential,
        system_row: SystemCredential,
    ) -> None:
        """Assert failed preflight left representative Gmail rows untouched."""
        user_row.refresh_from_db()
        system_row.refresh_from_db()
        self.assertEqual(user_row.type, 'gmail')
        self.assertEqual(bytes(user_row.encrypted_value), b'user-conflict-ciphertext')
        self.assertEqual(system_row.type, 'gmail')
        self.assertEqual(system_row.name, 'gmail-secondary')
        self.assertEqual(bytes(system_row.encrypted_value), b'system-conflict-ciphertext')

    def test_renames_both_tables_without_rewriting_ciphertext(self) -> None:
        user = get_user_model().objects.create_user(username='migration-user')
        user_row = UserCredential.objects.create(
            user=user,
            name='gmail-personal',
            type='gmail',
            encrypted_value=b'user-ciphertext',
        )
        system_row = SystemCredential.objects.create(
            name='default:gmail',
            type='gmail',
            is_default=True,
            encrypted_value=b'system-ciphertext',
        )

        self.rename(django_apps, None)

        user_row.refresh_from_db()
        system_row.refresh_from_db()
        self.assertEqual(user_row.type, 'google')
        self.assertEqual(bytes(user_row.encrypted_value), b'user-ciphertext')
        self.assertEqual(system_row.type, 'google')
        self.assertEqual(system_row.name, 'default:google')
        self.assertEqual(bytes(system_row.encrypted_value), b'system-ciphertext')

    def test_preflights_google_system_default_conflict(self) -> None:
        user_row, other_system_row = self._seed_nondefault_gmail_rows()
        SystemCredential.objects.create(
            name='default:gmail',
            type='gmail',
            is_default=True,
            encrypted_value=b'gmail-ciphertext',
        )
        SystemCredential.objects.create(
            name='platform-google',
            type='google',
            is_default=True,
            encrypted_value=b'google-ciphertext',
        )

        with self.assertRaisesMessage(
            RuntimeError,
            'cannot migrate gmail credential: a google system default already exists',
        ):
            self.rename(django_apps, None)

        self.assertTrue(SystemCredential.objects.filter(type='gmail', name='default:gmail').exists())
        self._assert_nondefault_gmail_rows_unchanged(user_row, other_system_row)

    def test_preflights_default_google_name_conflict(self) -> None:
        user_row, other_system_row = self._seed_nondefault_gmail_rows()
        SystemCredential.objects.create(
            name='default:gmail',
            type='gmail',
            is_default=True,
            encrypted_value=b'gmail-ciphertext',
        )
        SystemCredential.objects.create(
            name='default:google',
            type='google',
            encrypted_value=b'google-ciphertext',
        )

        with self.assertRaisesMessage(
            RuntimeError,
            'cannot migrate default:gmail: system credential default:google already exists',
        ):
            self.rename(django_apps, None)

        self.assertTrue(SystemCredential.objects.filter(type='gmail', name='default:gmail').exists())
        self._assert_nondefault_gmail_rows_unchanged(user_row, other_system_row)

    def test_leaves_noncanonical_default_gmail_name_untouched(self) -> None:
        unrelated = SystemCredential.objects.create(
            name='default:gmail',
            type='clickup',
            is_default=False,
            encrypted_value=b'unrelated-ciphertext',
        )

        self.rename(django_apps, None)

        unrelated.refresh_from_db()
        self.assertEqual(unrelated.name, 'default:gmail')
        self.assertEqual(unrelated.type, 'clickup')
        self.assertEqual(bytes(unrelated.encrypted_value), b'unrelated-ciphertext')


class TestUserCredentialHealthMigration(OTestCase):
    """Verify the health backfill classifies pre-existing rows by auth kind and content."""

    def setUp(self) -> None:
        """Load the generated migration's backfill function under test."""
        super().setUp()
        migration = import_module('apps.keys.migrations.0007_usercredential_health')
        self.backfill = migration.backfill_usercredential_health

    def test_adds_health_fields_without_a_data_rewrite_helper_for_ready_rows(self) -> None:
        """The generated schema operations add the two new columns with a ready default."""
        migration = import_module('apps.keys.migrations.0007_usercredential_health')
        operations = migration.Migration.operations
        added_fields = {
            operation.name: operation.field for operation in operations if isinstance(operation, migrations.AddField)
        }

        self.assertEqual(added_fields['health_status'].default, 'ready')
        self.assertEqual(added_fields['health_code'].default, '')
        self.assertTrue(any(isinstance(operation, migrations.RunPython) for operation in operations))

    def test_backfill_marks_empty_oauth_rows_not_connected(self) -> None:
        user = get_user_model().objects.create_user(username='migration-health-oauth')
        row = UserCredential.objects.create(
            user=user,
            name='google-oauth',
            type='google',
            auth_kind='oauth',
            encrypted_value=b'',
        )

        self.backfill(django_apps, None)

        row.refresh_from_db()
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'oauth_not_connected')

    def test_backfill_marks_empty_static_rows_value_empty(self) -> None:
        user = get_user_model().objects.create_user(username='migration-health-static')
        row = UserCredential.objects.create(
            user=user,
            name='openai-empty',
            type='openai',
            auth_kind='static',
            encrypted_value=b'',
        )

        self.backfill(django_apps, None)

        row.refresh_from_db()
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'value_empty')

    def test_backfill_leaves_non_empty_rows_ready(self) -> None:
        user = get_user_model().objects.create_user(username='migration-health-ready')
        static_row = UserCredential.objects.create(
            user=user,
            name='openai-set',
            type='openai',
            auth_kind='static',
            encrypted_value=b'ciphertext',
        )
        oauth_row = UserCredential.objects.create(
            user=user,
            name='google-connected',
            type='google',
            auth_kind='oauth',
            encrypted_value=b'grant-ciphertext',
        )

        self.backfill(django_apps, None)

        static_row.refresh_from_db()
        oauth_row.refresh_from_db()
        self.assertEqual(static_row.health_status, 'ready')
        self.assertEqual(static_row.health_code, '')
        self.assertEqual(oauth_row.health_status, 'ready')
        self.assertEqual(oauth_row.health_code, '')
