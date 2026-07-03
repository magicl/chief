# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Generated manually for revision item: remove user default credentials.

from django.db import migrations


def _delete_user_default_rows(apps, schema_editor) -> None:
    UserCredential = apps.get_model('keys', 'UserCredential')
    UserCredential.objects.filter(role='default').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('keys', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(_delete_user_default_rows, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='usercredential',
            name='keys_usercredential_default_per_type_uniq',
        ),
        migrations.RemoveField(
            model_name='usercredential',
            name='role',
        ),
    ]
