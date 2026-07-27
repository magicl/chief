# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Observability hooks for agent session runner lifecycles."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import local
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.runner.backends.base import RecordedActivity
    from libs.providers.llm.base import StreamResult
    from libs.tools.schema import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookSet:
    """Optional callbacks that observe a SessionRunner run without changing behavior."""

    on_run_start: Callable[[], None] | None = None
    on_run_end: Callable[[], None] | None = None
    on_generate_start: Callable[[list[dict[str, Any]], list[ToolDefinition]], None] | None = None
    on_generate_end: Callable[[StreamResult], None] | None = None
    on_tool_call_start: Callable[[dict[str, Any]], None] | None = None
    on_tool_call_end: Callable[[dict[str, Any], str], None] | None = None
    on_activity_created: Callable[[RecordedActivity], None] | None = None
    on_activity_updated: Callable[[RecordedActivity], None] | None = None
    on_status: Callable[[str], None] | None = None


@dataclass(frozen=True)
class _QueuedHookCall:
    """Capture one event and its registration snapshot at fire time."""

    hook_name: str
    args: tuple[Any, ...]
    hook_sets: tuple[HookSet, ...]
    on_base_exception: Callable[[BaseException], None] | None


class HookRegistry:
    """Dispatch hook events in deterministic breadth-first registration order."""

    def __init__(self) -> None:
        """Create an empty hook registry for one runner instance."""
        self._hook_sets: list[HookSet] = []
        self._dispatch_local = local()

    def add(self, hooks: HookSet) -> None:
        """Register callbacks to receive future runner lifecycle observations."""
        self._hook_sets.append(hooks)

    def fire(
        self,
        hook_name: str,
        *args: Any,
        on_base_exception: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Drain queued events, compensate their sources, then re-raise first cancellation."""
        call = _QueuedHookCall(hook_name, args, tuple(self._hook_sets), on_base_exception)
        queue: deque[_QueuedHookCall] | None = getattr(self._dispatch_local, 'queue', None)
        if queue is not None:
            queue.append(call)
            return

        queue = deque([call])
        self._dispatch_local.queue = queue
        first_fault: BaseException | None = None
        try:
            while queue:
                for fault in self._dispatch(queue.popleft()):
                    if first_fault is None:
                        first_fault = fault
        finally:
            del self._dispatch_local.queue
        if first_fault is not None:
            raise first_fault.with_traceback(first_fault.__traceback__) from None

    @staticmethod
    def _dispatch(call: _QueuedHookCall) -> list[BaseException]:
        """Deliver all callbacks and compensate once when base-level faults occur."""
        faults: list[BaseException] = []
        source_fault: BaseException | None = None
        for hooks in call.hook_sets:
            callback = getattr(hooks, call.hook_name)
            if callback is None:
                continue
            try:
                callback(*call.args)
            except Exception:  # pylint: disable=broad-except
                logger.exception('Session runner hook %s failed', call.hook_name)
            except BaseException as fault:
                faults.append(fault)
                if source_fault is None:
                    source_fault = fault
        if source_fault is not None and call.on_base_exception is not None:
            try:
                call.on_base_exception(source_fault)
            except BaseException as fault:
                faults.append(fault)
        return faults
