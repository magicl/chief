# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.admin import SystemCredentialAdminForm
from apps.keys.models import SystemCredential
from apps.keys.services import commands

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
