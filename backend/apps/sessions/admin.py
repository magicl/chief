# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from typing import Any

from apps.sessions.models import AgentSession, AgentSessionActivity
from django.contrib import admin


class AgentSessionActivityInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AgentSessionActivity
    fk_name = 'session'
    extra = 0
    readonly_fields = (
        'id',
        'seq',
        'revision',
        'kind',
        'status',
        'name',
        'summary',
        'details',
        'parent',
        'child_session',
        'model',
        'input_tokens',
        'output_tokens',
        'cost_usd',
        'latency_ms',
        'started_at',
        'ended_at',
        'created_at',
    )
    fields = readonly_fields
    ordering = ('seq',)

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        """Prevent activity creation through the session inline."""
        del request, obj
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """Prevent activity deletion through the session inline."""
        del request, obj
        return False


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('id', 'name', 'agent', 'status', 'created_at', 'started_at', 'ended_at')
    list_filter = ('status',)
    readonly_fields = ('created_at', 'started_at', 'ended_at')
    inlines = [AgentSessionActivityInline]


@admin.register(AgentSessionActivity)
class AgentSessionActivityAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('session', 'seq', 'kind', 'status', 'name', 'revision', 'created_at')
    list_filter = ('kind', 'status')
    readonly_fields = (
        'id',
        'session',
        'seq',
        'revision',
        'kind',
        'status',
        'name',
        'summary',
        'details',
        'parent',
        'child_session',
        'model',
        'input_tokens',
        'output_tokens',
        'cost_usd',
        'latency_ms',
        'started_at',
        'ended_at',
        'created_at',
    )

    def has_add_permission(self, request: Any) -> bool:
        """Prevent activity creation through its direct admin."""
        del request
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        """Prevent activity deletion through its direct admin."""
        del request, obj
        return False
