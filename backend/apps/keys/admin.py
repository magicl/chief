# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from typing import cast

from apps.keys import crypto
from apps.keys.models import SystemCredential, UserCredential
from apps.keys.services import commands
from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError


class SystemCredentialAdminForm(forms.ModelForm):  # type: ignore[type-arg]
    """Write-only secret field for staff; never displays stored plaintext."""

    secret = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 8,
                'style': 'font-family: ui-monospace, monospace; width: 100%;',
                'autocomplete': 'off',
            },
        ),
        required=False,
        help_text='Write-only plaintext. Paste multiline JSON for Gmail. Blank on edit keeps the existing value.',
    )

    class Meta:
        model = SystemCredential
        fields = ('name', 'type', 'is_default', 'secret')

    def save(self, commit: bool = True) -> SystemCredential:
        instance = super().save(commit=False)
        secret = self.cleaned_data.get('secret', '').strip()
        if secret:
            instance.encrypted_value = crypto.encrypt(secret)
            if instance.is_default:
                SystemCredential.objects.filter(type=instance.type, is_default=True).exclude(
                    pk=instance.pk,
                ).update(is_default=False)
            if commit:
                instance.save()
                self.save_m2m()
            return cast(SystemCredential, instance)
        if instance.pk and instance.is_default:
            commands.set_system_default(instance.type, '')
            return cast(SystemCredential, instance)
        if not instance.pk:
            raise ValidationError('Secret is required when creating a credential.')
        if commit:
            instance.save()
            self.save_m2m()
        return cast(SystemCredential, instance)


@admin.register(SystemCredential)
class SystemCredentialAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Staff UI for platform credential slots (metadata + write-only secret)."""

    form = SystemCredentialAdminForm
    list_display = ('name', 'type', 'is_default', 'is_set_display', 'updated_at')
    readonly_fields = ('is_set_display', 'created_at', 'updated_at')
    exclude = ('encrypted_value',)

    @admin.display(boolean=True, description='Set')
    def is_set_display(self, obj: SystemCredential) -> bool:
        return bool(obj.encrypted_value)


@admin.register(UserCredential)
class UserCredentialAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Read-only user credential metadata for staff support (no secret access)."""

    list_display = ('user', 'name', 'type', 'is_set_display', 'updated_at')
    readonly_fields = ('name', 'type', 'is_set_display', 'created_at', 'updated_at', 'user')
    exclude = ('encrypted_value',)

    def has_add_permission(self, request) -> bool:  # type: ignore[no-untyped-def]
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # type: ignore[no-untyped-def]
        return False

    @admin.display(boolean=True, description='Set')
    def is_set_display(self, obj: UserCredential) -> bool:
        return bool(obj.encrypted_value)
