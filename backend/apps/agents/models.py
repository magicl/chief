# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent domain models."""

from django.conf import settings
from django.db import models
from libs.agent_spec import AgentConfigSpec, load_spec

from olib.py.utils.uuid7 import uuid7


class Agent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='agents')
    name = models.CharField(max_length=255)
    identifier = models.CharField(max_length=255)
    config_source = models.CharField(max_length=255, default='ui')
    current_config = models.ForeignKey(
        'AgentConfig',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'identifier'], name='agents_agent_user_identifier_uniq'),
        ]

    def __str__(self) -> str:
        return self.name


class AgentConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='configs')
    source_rev = models.CharField(max_length=255, default='hardcoded-v0.1')
    dirty = models.BooleanField(default=False)
    fetched_at = models.DateTimeField(auto_now_add=True)
    spec = models.JSONField()
    spec_version = models.PositiveSmallIntegerField(default=0)

    def get_spec(self) -> AgentConfigSpec:
        return load_spec(self.spec, stored_version=self.spec_version)

    def __str__(self) -> str:
        return f'{self.agent.identifier}@{self.source_rev}'


class TriggerKind(models.TextChoices):
    SCHEDULE = 'schedule', 'Schedule'
    MANUAL = 'manual', 'Manual'
    AGENT = 'agent', 'Agent'
    QUEUE = 'queue', 'Queue'


class TriggerStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'


class Trigger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='triggers')
    agent_config = models.ForeignKey(AgentConfig, on_delete=models.CASCADE, related_name='triggers')
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=32, choices=TriggerKind.choices)
    status = models.CharField(max_length=32, choices=TriggerStatus.choices, default=TriggerStatus.ACTIVE)
    last_fired_at = models.DateTimeField(null=True, blank=True)
    spec = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['agent_config', 'name'],
                name='agents_trigger_config_name_uniq',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.agent.identifier}:{self.name}'
