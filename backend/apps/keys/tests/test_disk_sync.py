# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock, patch

from apps.keys import crypto
from apps.keys.models import (
    CredentialAuthKind,
    CredentialSource,
    CredentialStatus,
    UserCredential,
)
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

    def write_oauth(
        self,
        *,
        capabilities: tuple[str, ...] = ('drive_metadata', 'gmail_read'),
        filename: str = 'work-google.yaml',
    ) -> Path:
        """Write one Google OAuth declaration using capability identifiers."""
        path = self.keys_path / filename
        scopes = ''.join(f'  - {capability}\n' for capability in capabilities)
        path.write_text(
            'name: work-google\n' 'type: google\n' 'owner: alice\n' 'source: oauth\n' f'scopes:\n{scopes}',
            encoding='utf-8',
        )
        return path

    def write_dropbox_oauth(self, *, filename: str = 'team-dropbox.yaml') -> Path:
        """Write one Dropbox OAuth declaration using the single metadata capability."""
        path = self.keys_path / filename
        path.write_text(
            'name: team-dropbox\ntype: dropbox\nowner: alice\nsource: oauth\nscopes:\n  - files_metadata\n',
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
        self.assertEqual(row.health_status, 'ready')
        self.assertEqual(row.health_code, '')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')
        self.assertNotEqual(bytes(row.encrypted_value), b'sk-first')

    def test_empty_value_creates_disk_credential(self) -> None:
        """Accept a required credential value field whose YAML value is empty."""
        self.write_key(value='')

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), '')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'value_empty')

    def test_oauth_file_creates_unconnected_normalized_declaration(self) -> None:
        """Create an active disk OAuth row with stable provider capability order."""
        self.write_oauth()

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        row = UserCredential.objects.get(user=self.user, name='work-google')
        self.assertEqual(row.type, 'google')
        self.assertEqual(row.auth_kind, CredentialAuthKind.OAUTH)
        self.assertEqual(
            row.auth_config,
            {'provider': 'google', 'capabilities': ['gmail_read', 'drive_metadata']},
        )
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'oauth_not_connected')

    def test_oauth_format_and_capability_order_changes_preserve_grant(self) -> None:
        """Update provenance but retain a grant when normalized semantics are equal."""
        path = self.write_oauth()
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        grant = crypto.encrypt('refresh-grant-sentinel')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=grant)
        old_revision = row.source_rev
        path.write_text(
            'scopes: [gmail_read, drive_metadata]\n'
            'source: oauth\n'
            'owner: alice\n'
            'type: google\n'
            'name: work-google\n',
            encoding='utf-8',
        )

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), grant)
        self.assertNotEqual(row.source_rev, old_revision)
        self.assertEqual(
            row.auth_config,
            {'provider': 'google', 'capabilities': ['gmail_read', 'drive_metadata']},
        )

    def test_oauth_capability_change_clears_grant(self) -> None:
        """Clear an OAuth grant when the requested capability set changes."""
        self.write_oauth(capabilities=('gmail_read',))
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=crypto.encrypt('old-grant-sentinel'))
        self.write_oauth(capabilities=('gmail_send',))

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertEqual(row.auth_config, {'provider': 'google', 'capabilities': ['gmail_send']})

    def test_oauth_to_static_change_replaces_grant_and_metadata(self) -> None:
        """Replace an OAuth grant when a declaration changes authentication kind."""
        path = self.write_oauth(capabilities=('gmail_read',))
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=crypto.encrypt('old-grant-sentinel'))
        path.write_text(
            'name: work-google\ntype: google\nowner: alice\nvalue: service-account-json\n',
            encoding='utf-8',
        )

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(row.auth_kind, CredentialAuthKind.STATIC)
        self.assertEqual(row.auth_config, {})
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'service-account-json')

    def test_unchanged_oauth_restore_preserves_grant(self) -> None:
        """Restore a disabled semantically equal OAuth declaration with its grant."""
        path = self.write_oauth(capabilities=('gmail_read', 'drive_metadata'))
        content = path.read_text(encoding='utf-8')
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        grant = crypto.encrypt('restore-grant-sentinel')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=grant)
        path.unlink()
        sync_keys_dir()
        path.write_text(content, encoding='utf-8')

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(bytes(row.encrypted_value), grant)

    def test_changed_oauth_restore_clears_grant(self) -> None:
        """Clear a disabled OAuth grant when its restored declaration has changed."""
        path = self.write_oauth(capabilities=('gmail_read',))
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=crypto.encrypt('restore-grant-sentinel'))
        path.unlink()
        sync_keys_dir()
        self.write_oauth(capabilities=('drive_metadata',))

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(bytes(row.encrypted_value), b'')

    def test_dropbox_oauth_file_creates_unconnected_declaration_with_dropbox_provider(self) -> None:
        """Create an active disk OAuth row wired to the Dropbox provider, not Google."""
        self.write_dropbox_oauth()

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        row = UserCredential.objects.get(user=self.user, name='team-dropbox')
        self.assertEqual(row.type, 'dropbox')
        self.assertEqual(row.auth_kind, CredentialAuthKind.OAUTH)
        self.assertEqual(row.auth_config, {'provider': 'dropbox', 'capabilities': ['files_metadata']})
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'oauth_not_connected')

    def test_dropbox_oauth_unchanged_reupsert_preserves_connected_grant(self) -> None:
        """Re-upserting an unchanged Dropbox declaration must not disturb its grant."""
        self.write_dropbox_oauth()
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='team-dropbox')
        grant = crypto.encrypt('dropbox-refresh-grant-sentinel')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=grant)

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), grant)
        self.assertEqual(row.auth_config, {'provider': 'dropbox', 'capabilities': ['files_metadata']})

    def test_dropbox_oauth_to_static_change_clears_grant(self) -> None:
        """Clear a connected Dropbox grant when the declaration changes auth kind."""
        path = self.write_dropbox_oauth()
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='team-dropbox')
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=crypto.encrypt('dropbox-old-grant-sentinel'))
        path.write_text(
            'name: team-dropbox\ntype: dropbox\nowner: alice\n'
            'value: \'{"app_key":"a","app_secret":"b","refresh_token":"c"}\'\n',
            encoding='utf-8',
        )

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(row.auth_kind, CredentialAuthKind.STATIC)
        self.assertEqual(row.auth_config, {})
        self.assertNotEqual(bytes(row.encrypted_value), b'')
        self.assertNotEqual(crypto.decrypt(bytes(row.encrypted_value)), 'dropbox-old-grant-sentinel')

    def test_unknown_and_raw_oauth_scopes_persist_invalid_declaration_and_preserve_grant(self) -> None:
        """Downgrade non-catalog capability input to a health row without losing a grant."""
        path = self.write_oauth(capabilities=('gmail_read',))
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-google')
        grant_sentinel = 'stored-refresh-grant-sentinel'
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=crypto.encrypt(grant_sentinel))
        for invalid_scope in ('unknown_capability_sentinel', 'https://www.googleapis.com/auth/gmail.readonly'):
            with self.subTest(invalid_scope=invalid_scope):
                path.write_text(
                    'name: work-google\n'
                    'type: google\n'
                    'owner: alice\n'
                    'source: oauth\n'
                    f'scopes: [{invalid_scope}]\n',
                    encoding='utf-8',
                )
                with self.assertNoLogs('apps.keys.services.disk_sync', level='ERROR'):
                    report = sync_keys_dir()

                self.assertEqual(report.succeeded, 1)
                self.assertEqual(report.failed, 0)
                self.assertNotIn(invalid_scope, report.items[0].detail or '')
                row.refresh_from_db()
                self.assertEqual(row.health_status, 'needs_attention')
                self.assertEqual(row.health_code, 'invalid_declaration')
                self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), grant_sentinel)

    @patch('apps.keys.services.disk_sync.logger.exception')
    def test_duplicate_yaml_failure_retains_no_values(
        self,
        log_exception: MagicMock,
    ) -> None:
        """Keep duplicate YAML values out of result detail and logging state."""
        first_sentinel = 'duplicate-first-value-secret-sentinel'
        second_sentinel = 'duplicate-second-value-secret-sentinel'
        path = self.keys_path / 'duplicate.yaml'
        path.write_text(
            f'type: openai\nowner: alice\nvalue: {first_sentinel}\nvalue: {second_sentinel}\n',
            encoding='utf-8',
        )

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR') as captured:
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        log_exception.assert_not_called()
        log_output = '\n'.join(captured.output)
        retained = f'{report.items[0].detail}\n{log_output}'
        self.assertNotIn(first_sentinel, retained)
        self.assertNotIn(second_sentinel, retained)

    @patch('apps.keys.services.disk_sync.logger.exception')
    def test_valid_oauth_replaces_corrupt_stored_config_without_secret_trace(
        self,
        log_exception: MagicMock,
    ) -> None:
        """Clear a grant when malformed stored metadata cannot be semantically compared."""
        grant_sentinel = 'corrupt-row-grant-secret-sentinel'
        row = UserCredential.objects.create(
            user=self.user,
            name='work-google',
            type='google',
            encrypted_value=crypto.encrypt(grant_sentinel),
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={
                'provider': 'google',
                'capabilities': [['corrupt-capability-secret-sentinel']],
            },
            source=CredentialSource.DISK,
            source_path='keys/work-google.yaml',
            source_rev='sha256:old',
        )
        self.write_oauth(capabilities=('gmail_read',))

        report = sync_keys_dir()

        self.assertEqual(report.failed, 0)
        log_exception.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertEqual(row.auth_config, {'provider': 'google', 'capabilities': ['gmail_read']})

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

    def test_unknown_type_persists_a_needs_attention_row_without_error_logs(self) -> None:
        """An unregistered type still identifies a row instead of only failing sync."""
        self.write_key(type_name='mystery', value='sk-mystery')

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.type, 'mystery')
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'unknown_type')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-mystery')

    def test_gmail_type_persists_a_needs_attention_row_without_storing_error_logs(self) -> None:
        """A renamed gmail type still identifies and encrypts a static row."""
        self.write_key(type_name='gmail', value='ultra-secret')

        report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.type, 'gmail')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'unknown_type')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'ultra-secret')

    def test_missing_scopes_persists_invalid_declaration_row_without_error_logs(self) -> None:
        """A resolvable OAuth identity with no scopes becomes an invalid_declaration row."""
        path = self.keys_path / 'work-google.yaml'
        path.write_text(
            'name: work-google\ntype: google\nowner: alice\nsource: oauth\n',
            encoding='utf-8',
        )

        with self.assertNoLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-google')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'invalid_declaration')
        self.assertEqual(bytes(row.encrypted_value), b'')

    def test_extra_auth_kind_field_persists_invalid_declaration_row(self) -> None:
        """Any presence of the non-disk ``auth_kind`` field invalidates the declaration."""
        path = self.keys_path / 'work-openai.yaml'
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nauth_kind: static\nvalue: sk-first\n',
            encoding='utf-8',
        )

        with self.assertNoLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'invalid_declaration')
        self.assertEqual(bytes(row.encrypted_value), b'')

    def test_invalid_declaration_retains_prior_ciphertext_on_existing_row(self) -> None:
        """Recovering an existing row's identity keeps its ciphertext when a later edit is invalid."""
        path = self.write_key(value='sk-first')
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nauth_kind: static\nvalue: sk-first\n',
            encoding='utf-8',
        )

        with self.assertNoLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        row.refresh_from_db()
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'invalid_declaration')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-first')

    def test_fixed_yaml_recovers_invalid_declaration_to_ready(self) -> None:
        """Clear needs-attention health when a later scan finds a valid declaration."""
        path = self.write_key(value='sk-first')
        sync_keys_dir()
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nauth_kind: static\nvalue: sk-first\n',
            encoding='utf-8',
        )
        sync_keys_dir()
        row = UserCredential.objects.get(user=self.user, name='work-openai')
        self.assertEqual(row.health_code, 'invalid_declaration')
        path.write_text(
            'name: work-openai\ntype: openai\nowner: alice\nvalue: sk-recovered\n',
            encoding='utf-8',
        )

        with self.assertNoLogs('apps.keys.services.disk_sync', level='ERROR'):
            report = sync_keys_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        row.refresh_from_db()
        self.assertEqual(row.health_status, 'ready')
        self.assertEqual(row.health_code, '')
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'sk-recovered')

    def test_invalid_yaml_does_not_disable_other_present_key(self) -> None:
        self.write_key()
        sync_keys_dir()
        malformed = self.keys_path / 'malformed.yaml'
        malformed.write_text('type: openai\nowner: alice\nvalue: [ultra-secret\n', encoding='utf-8')

        with self.assertLogs('apps.keys.services.disk_sync', level='ERROR') as captured:
            report = sync_keys_dir()

        self.assertEqual(report.failed, 1)
        self.assertEqual(
            next(item.detail for item in report.items if not item.success),
            'YAMLError',
        )
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
