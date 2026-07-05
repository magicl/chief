# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Add human-readable Agent.name and backfill from identifier."""

from django.db import migrations, models


def copy_identifier_to_name(apps, schema_editor) -> None:
    """Populate ``name`` from ``identifier`` for existing agents."""
    Agent = apps.get_model('agents', 'Agent')
    for agent in Agent.objects.all().only('pk', 'identifier', 'name'):
        if not agent.name:
            agent.name = agent.identifier
            agent.save(update_fields=['name'])


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0003_agentconfig_spec_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='name',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(copy_identifier_to_name, migrations.RunPython.noop),
    ]
