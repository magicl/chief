# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic agent lifecycle hooks for outer packages to register against.

`apps.agents` owns materialize/delete orchestration but must not import
domain-specific side effects (vaults, etc.). Outer apps register callables here
from ``AppConfig.ready()``; materialize/delete only notify through this registry.
Handlers must never raise into the caller — each notification isolates failures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from libs.agent_spec import AgentConfigSpec

logger = logging.getLogger(__name__)

AgentMaterializedHandler = Callable[[UUID, int, AgentConfigSpec], None]
AgentDeletedHandler = Callable[[UUID], None]

_materialized_handlers: list[AgentMaterializedHandler] = []
_deleted_handlers: list[AgentDeletedHandler] = []


def register_agent_materialized_handler(handler: AgentMaterializedHandler) -> None:
    """Register a post-commit callback for agent config materialization."""
    if handler not in _materialized_handlers:
        _materialized_handlers.append(handler)


def register_agent_deleted_handler(handler: AgentDeletedHandler) -> None:
    """Register a post-commit callback for agent deletion."""
    if handler not in _deleted_handlers:
        _deleted_handlers.append(handler)


def clear_agent_lifecycle_handlers() -> None:
    """Remove all registered handlers (tests only; prefer ``isolated_lifecycle_handlers``)."""
    _materialized_handlers.clear()
    _deleted_handlers.clear()


@contextmanager
def isolated_lifecycle_handlers() -> Iterator[None]:
    """Temporarily clear handlers, then restore the previous registry (tests only).

    Preserves AppConfig-registered handlers (e.g. Obsidian) so later tests still
    see production wiring after an isolated unit test of the registry itself.
    """
    saved_materialized = list(_materialized_handlers)
    saved_deleted = list(_deleted_handlers)
    clear_agent_lifecycle_handlers()
    try:
        yield
    finally:
        clear_agent_lifecycle_handlers()
        _materialized_handlers.extend(saved_materialized)
        _deleted_handlers.extend(saved_deleted)


def notify_agent_materialized(agent_id: UUID, user_id: int, spec: AgentConfigSpec) -> None:
    """Invoke every registered materialize handler; log and continue on failure."""
    for handler in list(_materialized_handlers):
        try:
            handler(agent_id, user_id, spec)
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.exception('agent materialize lifecycle handler %r failed for %s', handler, agent_id)


def notify_agent_deleted(agent_id: UUID) -> None:
    """Invoke every registered delete handler; log and continue on failure."""
    for handler in list(_deleted_handlers):
        try:
            handler(agent_id)
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.exception('agent delete lifecycle handler %r failed for %s', handler, agent_id)
