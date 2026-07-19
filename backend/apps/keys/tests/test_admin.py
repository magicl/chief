# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.admin import SystemCredentialAdminForm, UserCredentialAdmin
from apps.keys.models import SystemCredential, UserCredential
from apps.keys.services import commands
from django.contrib.auth import get_user_model
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


class TestSystemCredentialAdmin(OTransactionTestCase):
    def test_clear_default_with_empty_secret_deletes_row(self) -> None:
        commands.set_system_default('openai', 'sk-system')
        row = SystemCredential.objects.get(type='openai', is_default=True)
        form = SystemCredentialAdminForm(
            data={
                'name': row.name,
                'type': row.type,
                'is_default': True,
                'secret': '',
            },
            instance=row,
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.assertFalse(SystemCredential.objects.filter(type='openai', is_default=True).exists())


class TestUserCredentialAdmin(OTransactionTestCase):
    """Verify staff credential metadata surfaces remain secret-free."""

    def test_authentication_metadata_is_read_only_and_ciphertext_is_excluded(self) -> None:
        """Admin exposes safe authentication metadata but never the encrypted value."""
        self.assertIn('auth_kind', UserCredentialAdmin.list_display)
        self.assertIn('oauth_provider_display', UserCredentialAdmin.list_display)
        self.assertIn('oauth_capabilities_display', UserCredentialAdmin.list_display)
        self.assertIn('auth_kind', UserCredentialAdmin.readonly_fields)
        self.assertIn('oauth_provider_display', UserCredentialAdmin.readonly_fields)
        self.assertIn('oauth_capabilities_display', UserCredentialAdmin.readonly_fields)
        self.assertIn('encrypted_value', UserCredentialAdmin.exclude)
        self.assertIn('auth_config', UserCredentialAdmin.exclude)
        self.assertNotIn('auth_config', UserCredentialAdmin.list_display)
        self.assertNotIn('auth_config', UserCredentialAdmin.readonly_fields)
        self.assertNotIn('encrypted_value', UserCredentialAdmin.list_display)
        self.assertNotIn('encrypted_value', UserCredentialAdmin.readonly_fields)

    def test_rendered_views_show_only_sanitized_authentication_metadata(self) -> None:
        """Rendered admin metadata excludes stored secrets and remains view-only."""
        staff = get_user_model().objects.create_superuser(username='key-admin', password='x')
        owner = get_user_model().objects.create_user(username='key-owner', password='x')
        safe_row = UserCredential.objects.create(
            user=owner,
            name='safe-google',
            type='google',
            auth_kind='oauth',
            auth_config={
                'provider': 'google',
                'capabilities': ['gmail_read', 'drive_metadata'],
            },
            encrypted_value=b'ENCRYPTED-GRANT-SENTINEL',
        )
        UserCredential.objects.create(
            user=owner,
            name='malformed-google',
            type='google',
            auth_kind='oauth',
            auth_config={
                'provider': 'unknown-provider',
                'capabilities': ['gmail_read'],
            },
            encrypted_value=b'OTHER-ENCRYPTED-SENTINEL',
        )
        self.client.force_login(staff)

        changelist = self.client.get(reverse('admin:keys_usercredential_changelist'))
        detail = self.client.get(reverse('admin:keys_usercredential_change', args=[safe_row.pk]))
        rendered = changelist.content.decode() + detail.content.decode()

        self.assertEqual(changelist.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(changelist, 'google')
        self.assertContains(changelist, 'gmail_read, drive_metadata')
        self.assertNotIn('unknown-provider', rendered)
        self.assertNotIn('ENCRYPTED-GRANT-SENTINEL', rendered)
        self.assertNotIn('OTHER-ENCRYPTED-SENTINEL', rendered)
        self.assertNotContains(detail, 'name="_save"')
