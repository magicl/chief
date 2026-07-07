# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shared source dedupe helpers for queue ingest.

Queue rows dedupe on ``(source, external_id)`` (see ``apps.queues``). Adapters use a
stable *resource id* per upstream item by default; ``config.dedupe`` (default ``true``)
keeps that id so an item is enqueued at most once per source even after terminal states.
Set ``dedupe: false`` to derive ``external_id`` from a change token so updates can
re-enter the queue (ClickUp ``date_updated``, Gmail ``historyId``).
"""

from __future__ import annotations

from typing import Any


def dedupe_enabled(config: dict[str, Any]) -> bool:
    """Return whether the source should use stable per-item ``external_id`` keys."""
    value = config.get('dedupe', True)
    if not isinstance(value, bool):
        raise ValueError('dedupe must be a boolean')
    return value


def validate_dedupe_config(config: dict[str, Any]) -> None:
    """Validate optional ``dedupe`` when present."""
    if 'dedupe' in config:
        dedupe_enabled(config)


def gmail_external_id(message_id: str, *, history_id: str | None = None, dedupe: bool = True) -> str:
    """Build the queue ``external_id`` for a Gmail message."""
    if dedupe:
        return message_id
    token = history_id if history_id else '0'
    return f'{message_id}:{token}'


def clickup_external_id(task_id: str, *, date_updated: str | None = None, dedupe: bool = True) -> str:
    """Build the queue ``external_id`` for a ClickUp task."""
    if dedupe:
        return task_id
    token = date_updated if date_updated else '0'
    return f'{task_id}:{token}'


def should_skip_known(*, dedupe: bool, external_id: str, known_external_ids: frozenset[str] | None) -> bool:
    """Return whether an adapter can skip fetch/put because the item was enqueued before."""
    return dedupe and known_external_ids is not None and external_id in known_external_ids
