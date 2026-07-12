# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Observability hooks for agent session runner lifecycles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookSet:
    """Optional callbacks that observe a SessionRunner run without changing behavior."""

    on_run_start: Callable[[], None] | None = None
    on_run_end: Callable[[], None] | None = None
    on_generate_start: Callable[[list[dict[str, Any]], list[Any]], None] | None = None
    on_generate_end: Callable[[Any], None] | None = None
    on_tool_call_start: Callable[[dict[str, Any]], None] | None = None
    on_tool_call_end: Callable[[dict[str, Any], str], None] | None = None
    on_event: Callable[[Any], None] | None = None
    on_status: Callable[[str], None] | None = None


class HookRegistry:
    """Stores HookSet instances and safely dispatches named hook callbacks."""

    def __init__(self) -> None:
        """Create an empty hook registry for one runner instance."""
        self._hook_sets: list[HookSet] = []

    def add(self, hooks: HookSet) -> None:
        """Register callbacks to receive future runner lifecycle observations."""
        self._hook_sets.append(hooks)

    def fire(self, hook_name: str, *args: Any) -> None:
        """Invoke matching callbacks, logging and swallowing hook failures."""
        for hooks in self._hook_sets:
            callback = getattr(hooks, hook_name)
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception:  # pylint: disable=broad-except
                logger.exception('Session runner hook %s failed', hook_name)
