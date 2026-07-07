# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""ClickUp source adapter: poll a list for tasks into a queue.

Filtering (list id, statuses, updated-after) lives in `config` — no triage logic here.
Emits the shared `{data, ref}` envelope so an agent can re-fetch the live task.
"""

from __future__ import annotations

from typing import Any

from libs.clients.clickup import ClickUpClient
from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter
from libs.sources.dedup import (
    clickup_external_id,
    dedupe_enabled,
    should_skip_known,
    validate_dedupe_config,
)

_DEFAULT_MAX_RESULTS = 50


def _status_name(task: dict[str, Any]) -> Any:
    """Return the status label whether ClickUp returns a string or a `{status: ...}` object."""
    status = task.get('status')
    if isinstance(status, dict):
        return status.get('status')
    return status


class ClickUpSourceAdapter(SourceAdapter):
    adapter_type = 'clickup'
    credential_type = 'clickup'

    def validate_config(self, config: dict[str, Any]) -> None:
        """Require a non-empty `list_id`; validate optional `statuses`/`max_results`."""
        list_id = config.get('list_id')
        if not isinstance(list_id, str) or not list_id:
            raise ValueError('list_id must be a non-empty string')
        statuses = config.get('statuses', [])
        if not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses):
            raise ValueError('statuses must be a list of strings')
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int) or max_results < 1:
            raise ValueError('max_results must be a positive integer')
        validate_dedupe_config(config)

    def poll(
        self,
        *,
        config: dict[str, Any],
        put_item: PutItemCallback,
        credential_supplier: SecretSupplier | None,
        known_external_ids: frozenset[str] | None = None,
    ) -> PollResult:
        """List tasks in the configured list and enqueue one `{data, ref}` envelope per task."""
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        dedupe = dedupe_enabled(config)
        client = ClickUpClient(token_supplier=credential_supplier or (lambda: None), config=config)
        tasks = client.list_tasks_up_to(
            list_id=config['list_id'],
            max_results=max_results,
            statuses=tuple(config.get('statuses', [])),
            include_closed=config.get('include_closed', False),
        )
        enqueued = 0
        for task in tasks:
            task_id = task['id']
            if should_skip_known(
                dedupe=dedupe,
                external_id=task_id,
                known_external_ids=known_external_ids,
            ):
                continue
            envelope = {
                'data': {
                    'id': task_id,
                    'name': task.get('name'),
                    'status': _status_name(task),
                    'list_id': config['list_id'],
                    'url': task.get('url'),
                    'date_updated': task.get('date_updated'),
                    'text_content': task.get('text_content'),
                },
                'ref': {'service': 'clickup', 'resource_type': 'task', 'resource_id': task_id},
            }
            date_updated = task.get('date_updated')
            ext_id = clickup_external_id(
                task_id,
                date_updated=date_updated if isinstance(date_updated, str) else None,
                dedupe=dedupe,
            )
            result = put_item(payload=envelope, external_id=ext_id)
            if result.created:
                enqueued += 1
        return PollResult(items_seen=len(tasks), items_enqueued=enqueued)
