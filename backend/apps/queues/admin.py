# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.queues.models import Queue, QueueItem, QueueItemAttempt, Source
from django.contrib import admin


class SourceInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Source
    extra = 0
    readonly_fields = (
        'id',
        'source_id',
        'adapter_type',
        'status',
        'credential_ref',
        'last_polled_at',
        'last_error',
        'last_error_at',
        'created_at',
    )
    fields = readonly_fields


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('queue_id', 'agent', 'max_attempts', 'created_at')
    search_fields = ('queue_id', 'agent__identifier')
    readonly_fields = ('id', 'created_at')
    inlines = [SourceInline]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ('source_id', 'queue', 'adapter_type', 'status', 'last_polled_at', 'last_error_at')
    list_filter = ('status', 'adapter_type')
    search_fields = ('source_id', 'queue__queue_id', 'queue__agent__identifier')
    readonly_fields = ('id', 'created_at', 'last_polled_at', 'last_error', 'last_error_at')


class QueueItemAttemptInline(admin.TabularInline):  # type: ignore[type-arg]
    model = QueueItemAttempt
    extra = 0
    can_delete = False
    readonly_fields = (
        'id',
        'attempt_number',
        'session',
        'outcome',
        'started_at',
        'ended_at',
        'detail',
    )
    fields = readonly_fields
    ordering = ('attempt_number',)


@admin.register(QueueItem)
class QueueItemAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        'id',
        'queue',
        'status',
        'attempt_count',
        'taken_by_session',
        'created_at',
    )
    list_filter = ('status',)
    search_fields = ('id', 'external_id', 'queue__queue_id', 'queue__agent__identifier')
    readonly_fields = (
        'id',
        'queue',
        'source',
        'external_id',
        'payload',
        'status',
        'attempt_count',
        'taken_by_session',
        'taken_at',
        'completed_at',
        'failure_reason',
        'created_at',
    )
    inlines = [QueueItemAttemptInline]
