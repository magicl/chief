# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.conf import settings
from django.db import models
from django.db.models import Q

from olib.py.utils.uuid7 import uuid7


class CredentialSource(models.TextChoices):
    """Identify the provider that last wrote a user credential."""

    DB = 'db', 'Database'
    DISK = 'disk', 'Disk'


class CredentialStatus(models.TextChoices):
    """Describe whether a user credential is available for resolution."""

    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'


class SystemCredential(models.Model):
    """Platform-scoped named credential; at most one per type may be marked default."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(max_length=64)
    type = models.CharField(max_length=32)
    is_default = models.BooleanField(default=False)
    encrypted_value = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name'], name='keys_systemcredential_name_uniq'),
            models.UniqueConstraint(
                fields=['type'],
                condition=Q(is_default=True),
                name='keys_systemcredential_default_per_type_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['type']),
        ]


class UserCredential(models.Model):
    """Per-user named credential (write-only in UI; referenced by name in agent config)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='credentials')
    name = models.CharField(max_length=64)
    type = models.CharField(max_length=32)
    encrypted_value = models.BinaryField()
    source = models.CharField(max_length=16, choices=CredentialSource.choices, default=CredentialSource.DB)
    source_path = models.CharField(max_length=512, blank=True, default='')
    source_rev = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=16, choices=CredentialStatus.choices, default=CredentialStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='keys_usercredential_user_name_uniq'),
        ]
        indexes = [
            models.Index(fields=['user', 'name']),
            models.Index(fields=['user', 'type']),
        ]
