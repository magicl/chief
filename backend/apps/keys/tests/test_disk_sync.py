# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock, patch

from apps.keys import crypto
from apps.keys.models import CredentialSource, CredentialStatus, UserCredential
from apps.keys.services.disk_sync import sync_keys_dir
from apps.keys.services.owner import resolve_owner
from django.contrib.auth import get_user_model
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class TestOwnerResolution(OTestCase):
    """Verify disk owner labels resolve without ambiguity."""

    def test_resolve_owner_prefers_exact_username(self) -> None:
        username_user = get_user_model().objects.create_user(
            username='alice@example.com',
            email='other@example.com',
        )
        get_user_model().objects.create_user(username='other', email='alice@example.com')

        self.assertEqual(resolve_owner('alice@example.com'), username_user)

    def test_resolve_owner_accepts_unique_email(self) -> None:
        email_user = get_user_model().objects.create_user(username='alice', email='alice@example.com')

        self.assertEqual(resolve_owner('alice@example.com'), email_user)

    def test_resolve_owner_returns_none_for_missing_or_shared_email(self) -> None:
        get_user_model().objects.create_user(username='alice', email='shared@example.com')
        get_user_model().objects.create_user(username='bob', email='shared@example.com')

        self.assertIsNone(resolve_owner('missing@example.com'))
        self.assertIsNone(resolve_owner('shared@example.com'))


class TestKeyDiskSync(OTestCase):
    """Verify local credential files synchronize into encrypted rows."""

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

    def test_progress_checkpoints_surround_files_and_precede_disable(self) -> None:
        """Invoke generic maintenance around file work and before missing-file disable."""
        path = self.write_key()
        calls: list[str] = []

        def sync_file(*_args: object, **_kwargs: object) -> MagicMock:
            """Record one file synchronization."""
            calls.append('file')
            return MagicMock()

        def disable_missing(**_kwargs: object) -> tuple[int, set[int]]:
            """Record the final missing-file reconciliation."""
            calls.append('disable')
            return 0, set()

        def record_progress() -> None:
            """Record one generic maintenance checkpoint."""
            calls.append('progress')

        with (
            patch(
                'apps.keys.services.disk_sync.sync_key_path',
                side_effect=sync_file,
            ),
            patch(
                'apps.keys.services.disk_sync.soft_disable_missing_disk_keys',
                side_effect=disable_missing,
            ),
        ):
            sync_keys_dir(progress=record_progress)

        self.assertTrue(path.exists())
        self.assertEqual(
            calls,
            ['progress', 'file', 'progress', 'progress', 'disable'],
        )

    @patch('apps.bus.resources.publish_resource_update')
    def test_valid_file_creates_encrypted_disk_credential(self, publish: MagicMock) -> None:
        """Report and publish the owner of a newly created disk credential."""
        self.write_key()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'keys')
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.source, CredentialSource.DISK)
        self.assertEqual(row.source_path, 'keys/work-openai.yaml')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')
        self.assertNotEqual(bytes(row.encrypted_value), b'sk-first')

    @patch('apps.bus.resources.publish_resource_update')
    def test_content_change_updates_ciphertext_and_revision(self, publish: MagicMock) -> None:
        """Report and publish replacement of changed disk content."""
        path = self.write_key(value='sk-first')
        with self.captureOnCommitCallbacks(execute=True):
            sync_keys_dir()
        original = UserCredential.objects.get(user=self.user, name='work-openai')
        old_ciphertext = bytes(original.encrypted_value)
        old_revision = original.source_rev
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nvalue: sk-second\n',
            encoding='utf-8',
        )
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'keys')
        original.refresh_from_db()
        self.assertNotEqual(bytes(original.encrypted_value), old_ciphertext)
        self.assertNotEqual(original.source_rev, old_revision)
        self.assertEqual(crypto.decrypt(bytes(original.encrypted_value)), 'sk-second')

    @patch('apps.bus.resources.publish_resource_update')
    def test_unchanged_content_has_no_mutation_or_event(self, publish: MagicMock) -> None:
        """Suppress owner changes and publication for identical disk content."""
        self.write_key()
        with self.captureOnCommitCallbacks(execute=True):
            sync_keys_dir()
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_keys_dir()

        self.assertEqual(report.changed_user_ids, set())
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertFalse(report.items[0].changed)
        publish.assert_not_called()

    def test_database_owned_conflict_records_failure_without_change(self) -> None:
        row = UserCredential.objects.create(
            user=self.user,
            name='work-openai',
            type='openai',
            encrypted_value=b'database-ciphertext',
        )
        self.write_key(value='disk-secret')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        row.refresh_from_db()
        self.assertEqual(row.source, CredentialSource.DB)
        self.assertEqual(bytes(row.encrypted_value), b'database-ciphertext')

    @patch('apps.bus.resources.publish_resource_update')
    def test_removed_files_disable_owner_keys_with_one_event(self, publish: MagicMock) -> None:
        """Group multiple missing credentials into one owner refresh event."""
        first_path = self.write_key()
        second_path = self.write_key('other.yaml', name='other-key', value='sk-other')
        with self.captureOnCommitCallbacks(execute=True):
            sync_keys_dir()
        first_path.unlink()
        second_path.unlink()
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_keys_dir()

        self.assertEqual(report.disabled, 2)
        self.assertEqual(report.disabled_user_ids, {self.user.pk})
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        publish.assert_called_once_with(self.user.pk, 'keys')
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.status, CredentialStatus.DISABLED)
        self.assertEqual(
            UserCredential.objects.filter(user=self.user, status=CredentialStatus.DISABLED).count(),
            2,
        )

    @patch('apps.bus.resources.publish_resource_update')
    def test_readded_unchanged_file_reports_restore_and_event(self, publish: MagicMock) -> None:
        """Treat restoration from disabled as a visible owner mutation."""
        path = self.write_key()
        unchanged_content = path.read_text(encoding='utf-8')
        with self.captureOnCommitCallbacks(execute=True):
            sync_keys_dir()
        path.unlink()
        with self.captureOnCommitCallbacks(execute=True):
            sync_keys_dir()
        path.write_text(unchanged_content, encoding='utf-8')
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_keys_dir()

        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'keys')
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)

    def test_missing_owner_records_failure_without_write(self) -> None:
        self.write_key(owner='nobody')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        self.assertFalse(UserCredential.objects.exists())

    def test_unknown_type_records_failure_without_write(self) -> None:
        self.write_key(type_name='mystery')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        self.assertFalse(UserCredential.objects.exists())

    def test_invalid_yaml_does_not_disable_other_present_key(self) -> None:
        self.write_key()
        sync_keys_dir()
        malformed = self.keys_path / 'malformed.yaml'
        malformed.write_text('type: openai\nowner: alice\nvalue: [ultra-secret\n', encoding='utf-8')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR') as captured:
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertNotIn('ultra-secret', '\n'.join(captured.output))

    def test_duplicate_identity_across_files_reports_conflict(self) -> None:
        """Keep the first file's value and report later duplicates as failures."""
        self.write_key('a-first.yaml', name='shared-key', value='sk-first')
        self.write_key('b-second.yaml', name='shared-key', value='sk-second')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(
            {item.detail for item in report.items if not item.success},
            {'duplicate identity'},
        )
        row = UserCredential.objects.get(user=self.user, name='shared-key')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')
        self.assertEqual(row.source_path, 'keys/a-first.yaml')
