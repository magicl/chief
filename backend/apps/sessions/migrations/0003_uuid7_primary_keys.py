# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~

from django.db import migrations, models

import olib.py.utils.uuid7


class Migration(migrations.Migration):

    dependencies = [
        ('agent_sessions', '0002_agent_config_cascade'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentsession',
            name='id',
            field=models.UUIDField(
                default=olib.py.utils.uuid7.uuid7,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name='agentsessionevent',
            name='id',
            field=models.UUIDField(
                default=olib.py.utils.uuid7.uuid7,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
    ]
