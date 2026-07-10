# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import shutil
from pathlib import Path
from tempfile import mkdtemp

from apps.keys import crypto
from apps.keys.models import CredentialSource, CredentialStatus, UserCredential
from apps.local_disk.key_sync import sync_keys_dir
from django.contrib.auth import get_user_model
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class TestKeySync(OTestCase):
    def setUp(self) -> None:
        """Create an isolated configured local root and credential owner."""
        super().setUp()
        self.root = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.keys_path = self.root / 'keys'
        self.keys_path.mkdir()
        self.settings_override = override_settings(CHIEF_LOCAL_DIR=str(self.root))
        self.settings_override.enable()
        self.user = get_user_model().objects.create_user(username='alice', email='alice@example.com')

    def tearDown(self) -> None:
        """Restore settings and remove the isolated local root."""
        self.settings_override.disable()
        super().tearDown()

    def write_key(
        self,
        filename: str = 'work-openai.yaml',
        *,
        value: str = 'sk-first',
        owner: str = 'alice',
        name: str = 'work-openai',
        type_name: str = 'openai',
    ) -> Path:
        """Write one valid credential YAML file and return its path."""
        path = self.keys_path / filename
        path.write_text(
            f'name: {name}\ntype: {type_name}\nowner: {owner}\nvalue: {value}\n',
            encoding='utf-8',
        )
        return path

    def test_valid_file_creates_encrypted_disk_credential(self) -> None:
        self.write_key()

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.source, CredentialSource.DISK)
        self.assertEqual(row.source_path, 'keys/work-openai.yaml')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')
        self.assertNotEqual(bytes(row.encrypted_value), b'sk-first')

    def test_content_change_updates_ciphertext_and_revision(self) -> None:
        path = self.write_key(value='sk-first')
        sync_keys_dir()
        original = UserCredential.objects.get(user=self.user, name='work-openai')
        old_ciphertext = bytes(original.encrypted_value)
        old_revision = original.source_rev
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nvalue: sk-second\n',
            encoding='utf-8',
        )

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        original.refresh_from_db()
        self.assertNotEqual(bytes(original.encrypted_value), old_ciphertext)
        self.assertNotEqual(original.source_rev, old_revision)
        self.assertEqual(crypto.decrypt(bytes(original.encrypted_value)), 'sk-second')

    def test_database_owned_conflict_records_failure_without_change(self) -> None:
        row = UserCredential.objects.create(
            user=self.user,
            name='work-openai',
            type='openai',
            encrypted_value=b'database-ciphertext',
        )
        self.write_key(value='disk-secret')

        with self.assertLogs('apps.local_disk.key_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        row.refresh_from_db()
        self.assertEqual(row.source, CredentialSource.DB)
        self.assertEqual(bytes(row.encrypted_value), b'database-ciphertext')

    def test_removed_file_soft_disables_bound_credential(self) -> None:
        path = self.write_key()
        sync_keys_dir()
        path.unlink()

        report = sync_keys_dir()

        self.assertEqual(report.disabled, 1)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.status, CredentialStatus.DISABLED)

    def test_missing_owner_records_failure_without_write(self) -> None:
        self.write_key(owner='nobody')

        with self.assertLogs('apps.local_disk.key_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        self.assertFalse(UserCredential.objects.exists())

    def test_invalid_yaml_does_not_disable_other_present_key(self) -> None:
        self.write_key()
        sync_keys_dir()
        malformed = self.keys_path / 'malformed.yaml'
        malformed.write_text('type: openai\nowner: alice\nvalue: [ultra-secret\n', encoding='utf-8')

        with self.assertLogs('apps.local_disk.key_sync', level='ERROR') as captured:
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertNotIn('ultra-secret', '\n'.join(captured.output))
