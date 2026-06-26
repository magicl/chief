# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.models import Agent, AgentConfig, Trigger
from django.contrib import admin


class AgentConfigInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AgentConfig
    extra = 0
    readonly_fields = ('id', 'source_rev', 'fetched_at', 'dirty')
    fields = ('id', 'source_rev', 'fetched_at', 'dirty')


class TriggerInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Trigger
    extra = 0
    readonly_fields = ('id', 'name', 'kind', 'status', 'agent_config')
    fields = ('id', 'name', 'kind', 'status', 'agent_config')


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('identifier', 'user', 'config_source', 'current_config')
    search_fields = ('identifier',)
    inlines = [AgentConfigInline, TriggerInline]


@admin.register(AgentConfig)
class AgentConfigAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('agent', 'source_rev', 'fetched_at', 'dirty')
    readonly_fields = ('spec',)


@admin.register(Trigger)
class TriggerAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('agent', 'name', 'kind', 'status', 'agent_config')
