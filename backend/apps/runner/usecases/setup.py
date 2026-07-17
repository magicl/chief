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
from uuid import uuid4

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookSet
from apps.runner.loop import SessionRunner
from apps.runner.usecases.observability import build_observability_hooks
from django.contrib.auth import get_user_model

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
    user_id: int | None = None,
) -> tuple[MemorySessionBackend, SessionRunner]:
    """Create MemorySessionBackend, push chat prompt, and wire runner hooks.

    Always runs as a Django user. When ``user_id`` is omitted, a disposable user
    is created for the run. Provider selection is left to the existing runner seam:
    tests and usecase harnesses patch ``apps.runner.loop.make_provider``.
    """
    if user_id is None:
        user_id = (
            get_user_model()
            .objects.create_user(username=f'usecase-{uuid4().hex}', password=uuid4().hex)
            .pk
        )
    backend = MemorySessionBackend(spec, user_id=user_id)
    backend.push_mailbox({'action': 'chat', 'content': prompt})

    runner = SessionRunner(backend, client_factories=client_factories)
    runner.add_hook(build_observability_hooks(partition=partition, log_writer=log_writer))
    if hooks_extra is not None:
        runner.add_hook(hooks_extra)

    return backend, runner
