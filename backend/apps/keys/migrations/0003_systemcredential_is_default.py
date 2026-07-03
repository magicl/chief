# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Generated manually: system credentials are always named; default is a flag.

from django.db import migrations, models
from django.db.models import Q


def _migrate_role_to_is_default(apps, schema_editor) -> None:
    SystemCredential = apps.get_model('keys', 'SystemCredential')
    for row in SystemCredential.objects.all():
        row.is_default = row.role == 'default'
        row.save(update_fields=['is_default'])


class Migration(migrations.Migration):
    dependencies = [
        ('keys', '0002_remove_usercredential_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemcredential',
            name='is_default',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(_migrate_role_to_is_default, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='systemcredential',
            name='keys_systemcredential_default_per_type_uniq',
        ),
        migrations.RemoveField(
            model_name='systemcredential',
            name='role',
        ),
        migrations.AddConstraint(
            model_name='systemcredential',
            constraint=models.UniqueConstraint(
                condition=Q(('is_default', True)),
                fields=('type',),
                name='keys_systemcredential_default_per_type_uniq',
            ),
        ),
    ]
