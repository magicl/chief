# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.services import commands
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


class TestKeysPage(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='keys-user', password='test')

    def test_requires_login(self) -> None:
        response = self.client.get(reverse('settings_keys'))
        self.assertEqual(response.status_code, 302)

    def test_shows_set_not_set_without_secret_material(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'openai-work', 'openai', 'sk-hidden')
        response = self.client.get(reverse('settings_keys'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Set', response.content)
        self.assertNotIn(b'sk-hidden', response.content)
        self.assertNotIn(b'Replace', response.content)

    def test_add_form_requires_type_before_secret_fields(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'Select a credential type', response.content)
        self.assertIn(b'Choose a type above', response.content)
        self.assertIn(b'credential-guides-data', response.content)

    def test_shows_gmail_setup_instructions_in_page_data(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'domain-wide delegation', response.content)
        self.assertIn(b'gmail.modify', response.content)

    def test_post_add_named(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'gmail-personal', 'type': 'gmail', 'secret': 'tok'},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'gmail-personal', response.content)

    def test_post_add_multiline_gmail_json(self) -> None:
        self.client.force_login(self.user)
        secret = '{\n  "type": "service_account",\n  "client_email": "sa@example.com"\n}\n'
        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'gmail-sa', 'type': 'gmail', 'secret': secret},
        )
        self.assertEqual(response.status_code, 302)
        from apps.keys.services.queries import resolve_secret

        stored = resolve_secret(self.user.pk, 'gmail-sa', expected_type='gmail')
        self.assertIn('\n', stored)
        self.assertIn('service_account', stored)

    def test_post_delete_named(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'clickup', 'clickup', 'tok')
        response = self.client.post(reverse('settings_keys_delete_named', kwargs={'name': 'clickup'}))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertNotIn(b'<code>clickup</code>', response.content)

    def test_disk_key_shows_source_without_delete_control(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, '<code>disk-openai</code>', html=True)
        self.assertContains(response, 'Disk')
        self.assertNotContains(
            response,
            reverse('settings_keys_delete_named', kwargs={'name': 'disk-openai'}),
        )

    def test_disabled_disk_key_shows_disabled_status(self) -> None:
        """Render disabled metadata instead of treating encrypted content as set."""
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )
        self.user.credentials.filter(name='disk-openai').update(status='disabled')

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, '<span class="pill waiting">Disabled</span>', html=True)

    def test_post_add_cannot_replace_disk_key(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )

        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'disk-openai', 'type': 'openai', 'secret': 'sk-ui'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'disk-sourced credential is read-only', response.content)
        row = self.user.credentials.get(name='disk-openai')
        self.assertEqual(row.source, 'disk')
        self.assertEqual(row.source_path, 'keys/disk-openai.yaml')

    def test_post_delete_cannot_remove_disk_key(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )

        response = self.client.post(
            reverse('settings_keys_delete_named', kwargs={'name': 'disk-openai'}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'disk-sourced credential is read-only', response.content)
        self.assertTrue(self.user.credentials.filter(name='disk-openai').exists())

    def test_update_endpoint_removed(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'openai-work', 'openai', 'sk-old')
        response = self.client.post('/settings/keys/named/openai-work/', {'secret': 'sk-new'})
        self.assertEqual(response.status_code, 404)
