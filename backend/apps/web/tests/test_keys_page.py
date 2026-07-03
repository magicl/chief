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
        self.assertNotIn(b'name="secret" value="', response.content)

    def test_post_add_named(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'gmail-personal', 'type': 'gmail', 'secret': 'tok'},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'gmail-personal', response.content)

    def test_post_delete_named(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'clickup', 'clickup', 'tok')
        response = self.client.post(reverse('settings_keys_delete_named', kwargs={'name': 'clickup'}))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertNotIn(b'<code>clickup</code>', response.content)
