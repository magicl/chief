# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery tasks for queue polling and stale item release."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from apps.queues.models import Source, SourceStatus
from apps.queues.services import commands as queue_commands
from celery import shared_task
from django.utils import timezone
from libs.sources.base import PutItemResult

logger = logging.getLogger(__name__)

_MAX_LAST_ERROR_LEN = 2000


@shared_task(ignore_result=True)
def poll_source(source_pk: str) -> None:
    """Run one poll cycle for a ``Source`` row via its registered adapter."""
    source = Source.objects.select_related('queue', 'queue__agent').get(pk=UUID(source_pk))
    if source.status != SourceStatus.ACTIVE:
        return

    try:
        from apps.keys.services.queries import make_secret_supplier
        from libs.sources.registry import get_adapter

        adapter = get_adapter(source.adapter_type)
        if adapter is None:
            raise ValueError(f'unknown adapter type {source.adapter_type!r}')

        credential_supplier = None
        cred_type = getattr(adapter, 'credential_type', None)
        if source.credential_ref and cred_type:
            credential_supplier = make_secret_supplier(
                source.queue.agent.user_id,
                name=source.credential_ref,
                type=cred_type,
            )

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Adapter callback: enqueue via commands with *source* bound for dedup."""
            result = queue_commands.put_item(
                queue=source.queue,
                source=source,
                payload=payload,
                external_id=external_id,
            )
            return PutItemResult(item_id=result.item_id, created=result.created)

        adapter.poll(
            config=source.config,
            put_item=put_item,
            credential_supplier=credential_supplier,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception('poll_source failed for source %s', source.pk)
        source.last_error = str(exc)[:_MAX_LAST_ERROR_LEN]
        source.last_error_at = timezone.now()
        source.save(update_fields=['last_error', 'last_error_at'])
        return

    source.last_polled_at = timezone.now()
    source.last_error = None
    source.last_error_at = None
    source.save(update_fields=['last_polled_at', 'last_error', 'last_error_at'])


@shared_task(ignore_result=True)
def release_stale_items() -> None:
    """Celery beat entry: reclaim queue items held past min/early/long thresholds."""
    from apps.queues.services.commands import release_stale_items as release_command

    release_command()
