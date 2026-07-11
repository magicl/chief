# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Factory helpers for in-memory usecase runs.

Callers patch ``apps.runner.loop.make_provider`` to inject ``FakeProvider`` plans or
real provider factories for a particular usecase run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookSet
from apps.runner.loop import SessionRunner
from apps.runner.usecases.observability import build_observability_hooks

# isort: split

from libs.agent_spec import AgentConfigSpec

from olib.py.eval import EventLogWriter, RunPartition


def build_memory_session_runner(
    *,
    spec: AgentConfigSpec,
    client_factories: dict[str, Callable[..., Any]],
    partition: RunPartition,
    log_writer: EventLogWriter,
    prompt: str,
    hooks_extra: HookSet | None = None,
) -> tuple[MemorySessionBackend, SessionRunner]:
    """Create MemorySessionBackend(user_id=None), push chat prompt, and wire runner hooks.

    This helper intentionally leaves provider selection to the existing runner seam:
    tests and usecase harnesses patch ``apps.runner.loop.make_provider`` to supply a
    ``FakeProvider`` or real provider for the run.
    """
    backend = MemorySessionBackend(spec, user_id=None)
    backend.push_mailbox({'action': 'chat', 'content': prompt})

    runner = SessionRunner(backend, client_factories=client_factories)
    runner.add_hook(build_observability_hooks(partition=partition, log_writer=log_writer))
    if hooks_extra is not None:
        runner.add_hook(hooks_extra)

    return backend, runner
