# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~

from django.db import migrations, models

import olib.py.utils.uuid7


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agent',
            name='id',
            field=models.UUIDField(
                default=olib.py.utils.uuid7.uuid7,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name='agentconfig',
            name='id',
            field=models.UUIDField(
                default=olib.py.utils.uuid7.uuid7,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name='trigger',
            name='id',
            field=models.UUIDField(
                default=olib.py.utils.uuid7.uuid7,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
