# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import MagicMock, patch

from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import CredentialStatus, SystemCredential, UserCredential
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
            commands.upsert_user_named(user.pk, 'default:evil', 'gmail', 'token')

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
