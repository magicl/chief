# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import MagicMock, patch

from apps.keys import crypto
from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import (
    CredentialAuthKind,
    CredentialStatus,
    SystemCredential,
    UserCredential,
)
from apps.keys.services import commands
from django.contrib.auth import get_user_model
from django.db import transaction

from olib.py.django.test.cases import OTestCase, OTransactionTestCase


class TestCredentialCommands(OTestCase):
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
        self.assertEqual(row.health_status, 'ready')
        self.assertEqual(row.health_code, '')

    def test_create_user_oauth_starts_unconnected(self) -> None:
        """A newly declared database OAuth credential starts needing a grant."""
        user = get_user_model().objects.create_user(username='cmd-oauth-user', password='x')

        meta = commands.create_user_oauth(
            user.pk,
            'google-work',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )

        self.assertFalse(meta.is_set)
        row = UserCredential.objects.get(user_id=user.pk, name='google-work')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'oauth_not_connected')

    def test_upsert_named_resets_existing_oauth_declaration_to_static(self) -> None:
        """Replacing a database OAuth row explicitly clears its declaration metadata."""
        user = get_user_model().objects.create_user(username='cmd-static-reset', password='x')
        row = UserCredential.objects.create(
            user=user,
            name='google-work',
            type='google',
            encrypted_value=b'old-oauth-grant',
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
        )

        commands.upsert_user_named(user.pk, row.name, 'google', 'service-account-json')

        row.refresh_from_db()
        self.assertEqual(row.auth_kind, CredentialAuthKind.STATIC)
        self.assertEqual(row.auth_config, {})

    def test_upsert_named_refuses_to_replace_disk_credential(self) -> None:
        """Keep disk-owned credential content and provenance immutable to UI writes."""
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

        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'openai-work', 'openai', 'sk-user-key')

        row = UserCredential.objects.get(user_id=user.pk, name='openai-work')
        self.assertEqual(row.encrypted_value, b'old')
        self.assertEqual(row.source, 'disk')
        self.assertEqual(row.source_path, 'keys/openai-work.yaml')
        self.assertEqual(row.source_rev, 'sha256:old')
        self.assertEqual(row.status, 'disabled')

    def test_upsert_named_rejects_reserved_prefix(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user2', password='x')
        with self.assertRaises(KeyValidationError):
            commands.upsert_user_named(user.pk, 'default:evil', 'google', 'token')

    @patch('apps.bus.resources.publish_resource_update')
    def test_upsert_named_publishes_after_commit(self, publish: MagicMock) -> None:
        """Notify the owner only after a UI credential write commits."""
        user = get_user_model().objects.create_user(username='notify-key-user')

        with self.captureOnCommitCallbacks(execute=True):
            commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')

        publish.assert_called_once_with(user.pk, 'keys')

    @patch('apps.bus.resources.publish_resource_update')
    def test_disk_upsert_reports_create_and_publishes(self, publish: MagicMock) -> None:
        """Report and publish a newly created disk credential."""
        user = get_user_model().objects.create_user(username='notify-disk-create')

        with self.captureOnCommitCallbacks(execute=True):
            metadata, changed = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'secret',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:create',
            )

        self.assertEqual(metadata.name, 'disk-key')
        self.assertTrue(changed)
        publish.assert_called_once_with(user.pk, 'keys')
        row = UserCredential.objects.get(user=user, name='disk-key')
        self.assertEqual(row.health_status, 'ready')
        self.assertEqual(row.health_code, '')

    def test_disk_upsert_with_empty_secret_needs_attention(self) -> None:
        """Flag a static disk declaration whose secret is empty as needing attention."""
        user = get_user_model().objects.create_user(username='disk-empty-secret')

        commands.upsert_user_named_from_disk(
            user.pk,
            'disk-key',
            'openai',
            '',
            source_path='keys/disk-key.yaml',
            source_rev='sha256:empty',
        )

        row = UserCredential.objects.get(user=user, name='disk-key')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'value_empty')

    @patch('apps.bus.resources.publish_resource_update')
    def test_unchanged_disk_upsert_does_not_publish(self, publish: MagicMock) -> None:
        """Suppress refresh hints when disk provenance and state are unchanged."""
        user = get_user_model().objects.create_user(username='notify-disk-unchanged')
        with self.captureOnCommitCallbacks(execute=True):
            _, created = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'secret',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:same',
            )
        self.assertTrue(created)
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            metadata, changed = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'secret',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:same',
            )

        self.assertEqual(metadata.name, 'disk-key')
        self.assertFalse(changed)
        publish.assert_not_called()

    @patch('apps.bus.resources.publish_resource_update')
    def test_disk_upsert_reports_content_change_and_publishes(self, publish: MagicMock) -> None:
        """Report and publish a changed disk credential revision."""
        user = get_user_model().objects.create_user(username='notify-disk-change')
        with self.captureOnCommitCallbacks(execute=True):
            _, created = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'first',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:first',
            )
        self.assertTrue(created)
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            _, changed = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'second',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:second',
            )

        self.assertTrue(changed)
        publish.assert_called_once_with(user.pk, 'keys')

    @patch('apps.bus.resources.publish_resource_update')
    def test_disk_upsert_reports_restore_and_publishes(self, publish: MagicMock) -> None:
        """Report and publish restoration of a disabled disk credential."""
        user = get_user_model().objects.create_user(username='notify-disk-restore')
        with self.captureOnCommitCallbacks(execute=True):
            _, created = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'secret',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:same',
            )
        self.assertTrue(created)
        UserCredential.objects.filter(user=user, name='disk-key').update(status=CredentialStatus.DISABLED)
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            _, changed = commands.upsert_user_named_from_disk(
                user.pk,
                'disk-key',
                'openai',
                'secret',
                source_path='keys/disk-key.yaml',
                source_rev='sha256:same',
            )

        self.assertTrue(changed)
        publish.assert_called_once_with(user.pk, 'keys')

    def test_disk_oauth_upsert_preserves_grant_for_normalized_semantics(self) -> None:
        """Preserve ciphertext across revision, ordering, and disabled-state changes."""
        user = get_user_model().objects.create_user(username='disk-oauth-semantic')
        commands.upsert_user_named_from_disk(
            user.pk,
            'work-google',
            'google',
            None,
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['drive_metadata', 'gmail_read']},
            source_path='keys/work-google.yaml',
            source_rev='sha256:first',
        )
        row = UserCredential.objects.get(user=user, name='work-google')
        grant = b'encrypted-grant-sentinel'
        UserCredential.objects.filter(pk=row.pk).update(
            encrypted_value=grant,
            status=CredentialStatus.DISABLED,
        )

        _, changed = commands.upsert_user_named_from_disk(
            user.pk,
            'work-google',
            'google',
            None,
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'capabilities': ['gmail_read', 'drive_metadata'], 'provider': 'google'},
            source_path='keys/work-google.yaml',
            source_rev='sha256:second',
        )

        self.assertTrue(changed)
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), grant)
        self.assertEqual(row.status, CredentialStatus.ACTIVE)
        self.assertEqual(row.source_rev, 'sha256:second')
        self.assertEqual(row.health_status, 'ready')
        self.assertEqual(row.health_code, '')

    def test_disk_oauth_upsert_clears_grant_for_type_change(self) -> None:
        """Replace ciphertext when a disk declaration changes type and auth kind."""
        user = get_user_model().objects.create_user(username='disk-oauth-type-change')
        row = UserCredential.objects.create(
            user=user,
            name='work-key',
            type='google',
            encrypted_value=b'encrypted-grant-sentinel',
            auth_kind=CredentialAuthKind.OAUTH,
            auth_config={'provider': 'google', 'capabilities': ['gmail_read']},
            source='disk',
            source_path='keys/work-key.yaml',
            source_rev='sha256:first',
        )

        commands.upsert_user_named_from_disk(
            user.pk,
            'work-key',
            'openai',
            'new-static-secret',
            auth_kind=CredentialAuthKind.STATIC,
            auth_config={},
            source_path='keys/work-key.yaml',
            source_rev='sha256:second',
        )

        row.refresh_from_db()
        self.assertEqual(row.type, 'openai')
        self.assertEqual(row.auth_kind, CredentialAuthKind.STATIC)
        self.assertEqual(row.auth_config, {})
        self.assertEqual(crypto.decrypt(bytes(row.encrypted_value)), 'new-static-secret')

    def test_stored_oauth_corruption_is_rejected_before_provider_normalization(self) -> None:
        """Treat structurally malformed stored capabilities as non-semantic metadata."""
        user = get_user_model().objects.create_user(username='disk-oauth-corruption')
        malformed_capabilities: tuple[list[object], ...] = (
            [['nested-secret-sentinel']],
            [{'nested-secret-sentinel': 'value'}],
            [7],
            [],
            [''],
            ['   '],
        )
        for index, capabilities in enumerate(malformed_capabilities):
            with self.subTest(capabilities=capabilities):
                row = UserCredential.objects.create(
                    user=user,
                    name=f'work-google-{index}',
                    type='google',
                    encrypted_value=b'encrypted-grant-sentinel',
                    auth_kind=CredentialAuthKind.OAUTH,
                    auth_config={'provider': 'google', 'capabilities': capabilities},
                    source='disk',
                )
                with patch('apps.keys.oauth.services.normalize_auth_config') as normalize:
                    normalized = commands._existing_disk_auth_config(row)  # pylint: disable=protected-access

                self.assertIsNone(normalized)
                normalize.assert_not_called()

    def test_upsert_disk_health_creates_identifiable_row_without_secret(self) -> None:
        """An invalid_declaration outcome with no prior row persists an empty secret."""
        user = get_user_model().objects.create_user(username='disk-health-create')

        metadata, changed = commands.upsert_disk_health(
            user.pk,
            'broken-key',
            'openai',
            None,
            health_status='needs_attention',
            health_code='invalid_declaration',
            source_path='keys/broken-key.yaml',
            source_rev='sha256:broken',
        )

        self.assertTrue(changed)
        self.assertFalse(metadata.is_set)
        row = UserCredential.objects.get(user=user, name='broken-key')
        self.assertEqual(bytes(row.encrypted_value), b'')
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'invalid_declaration')

    def test_upsert_disk_health_preserves_prior_auth_material(self) -> None:
        """A later invalid edit keeps a disk row's previous auth kind and ciphertext."""
        user = get_user_model().objects.create_user(username='disk-health-preserve')
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
        row = UserCredential.objects.get(user=user, name='work-google')
        grant = b'encrypted-grant-sentinel'
        UserCredential.objects.filter(pk=row.pk).update(encrypted_value=grant)

        commands.upsert_disk_health(
            user.pk,
            'work-google',
            'google',
            None,
            health_status='needs_attention',
            health_code='invalid_declaration',
            source_path='keys/work-google.yaml',
            source_rev='sha256:second',
        )

        row.refresh_from_db()
        self.assertEqual(row.auth_kind, CredentialAuthKind.OAUTH)
        self.assertEqual(row.auth_config, {'provider': 'google', 'capabilities': ['gmail_read']})
        self.assertEqual(bytes(row.encrypted_value), grant)
        self.assertEqual(row.health_status, 'needs_attention')
        self.assertEqual(row.health_code, 'invalid_declaration')

    def test_upsert_disk_health_truncates_overlong_type_name(self) -> None:
        """Cap an unregistered disk type name so storage limits cannot be exceeded."""
        user = get_user_model().objects.create_user(username='disk-health-truncate')
        overlong = 'x' * 100

        commands.upsert_disk_health(
            user.pk,
            'overlong-type',
            overlong,
            None,
            health_status='needs_attention',
            health_code='unknown_type',
            source_path='keys/overlong-type.yaml',
            source_rev='sha256:overlong',
        )

        row = UserCredential.objects.get(user=user, name='overlong-type')
        self.assertEqual(row.type, overlong[:32])

    def test_upsert_disk_health_rejects_database_owned_conflict(self) -> None:
        """Refuse to downgrade a database-owned credential's health from disk sync."""
        user = get_user_model().objects.create_user(username='disk-health-conflict')
        UserCredential.objects.create(
            user=user,
            name='db-owned',
            type='openai',
            encrypted_value=b'database-ciphertext',
        )

        with self.assertRaises(KeyValidationError):
            commands.upsert_disk_health(
                user.pk,
                'db-owned',
                'openai',
                None,
                health_status='needs_attention',
                health_code='invalid_declaration',
                source_path='keys/db-owned.yaml',
                source_rev='sha256:conflict',
            )

    def test_delete_user_credential_idempotent_missing(self) -> None:
        user = get_user_model().objects.create_user(username='cmd-user3', password='x')
        with self.assertRaises(KeyNotFoundError):
            commands.delete_user_credential(user.pk, 'missing')

    @patch('apps.bus.resources.publish_resource_update')
    def test_delete_user_credential_publishes_after_commit(self, publish: MagicMock) -> None:
        """Notify the owner once after deleting an existing credential."""
        user = get_user_model().objects.create_user(username='notify-key-delete')
        with self.captureOnCommitCallbacks(execute=True):
            commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            commands.delete_user_credential(user.pk, 'work')

        publish.assert_called_once_with(user.pk, 'keys')

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

    @patch('apps.bus.resources.publish_resource_update')
    def test_system_credentials_do_not_publish_user_events(self, publish: MagicMock) -> None:
        """Keep system credential mutations off user-scoped channels."""
        with self.captureOnCommitCallbacks(execute=True):
            commands.set_system_default('anthropic', 'sk-a')
            commands.delete_system_credential('default:anthropic')

        publish.assert_not_called()


class TestCredentialCommandCommitTiming(OTransactionTestCase):
    """Verify resource hints follow the surrounding transaction outcome."""

    @patch('apps.bus.resources.publish_resource_update')
    def test_publisher_failure_does_not_fail_committed_upsert(self, publish: MagicMock) -> None:
        """Keep a committed credential write successful when transport is unavailable."""
        user = get_user_model().objects.create_user(username='publisher-failure')
        publish.side_effect = RuntimeError('transport unavailable')

        metadata = commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')

        self.assertEqual(metadata.name, 'work')
        self.assertTrue(UserCredential.objects.filter(user=user, name='work').exists())
        publish.assert_called_once_with(user.pk, 'keys')

    @patch('apps.bus.resources.publish_resource_update')
    def test_outer_commit_defers_publication(self, publish: MagicMock) -> None:
        """Publish only after the caller's outer transaction commits."""
        user = get_user_model().objects.create_user(username='outer-commit')

        with transaction.atomic():
            commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')
            publish.assert_not_called()

        publish.assert_called_once_with(user.pk, 'keys')

    @patch('apps.bus.resources.publish_resource_update')
    def test_outer_rollback_suppresses_publication(self, publish: MagicMock) -> None:
        """Discard resource publication when the caller rolls back its write."""
        user = get_user_model().objects.create_user(username='outer-rollback')

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                commands.upsert_user_named(user.pk, 'work', 'openai', 'secret')
                raise RuntimeError('roll back')

        self.assertFalse(UserCredential.objects.filter(user=user, name='work').exists())
        publish.assert_not_called()
