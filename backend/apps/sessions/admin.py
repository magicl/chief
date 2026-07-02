# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.sessions.models import AgentSession, AgentSessionEvent
from django.contrib import admin


class AgentSessionEventInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AgentSessionEvent
    extra = 0
    readonly_fields = (
        'seq',
        'kind',
        'payload',
        'model',
        'input_tokens',
        'output_tokens',
        'cost_usd',
        'latency_ms',
        'created_at',
    )
    fields = readonly_fields
    ordering = ('seq',)


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('id', 'name', 'agent', 'status', 'created_at', 'started_at', 'ended_at')
    list_filter = ('status',)
    readonly_fields = ('created_at', 'started_at', 'ended_at')
    inlines = [AgentSessionEventInline]


@admin.register(AgentSessionEvent)
class AgentSessionEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('session', 'seq', 'kind', 'model', 'created_at')
    list_filter = ('kind',)
    readonly_fields = (
        'session',
        'seq',
        'kind',
        'payload',
        'model',
        'input_tokens',
        'output_tokens',
        'cost_usd',
        'latency_ms',
        'created_at',
    )
