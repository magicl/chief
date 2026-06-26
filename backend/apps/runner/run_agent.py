# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Headless single-turn agent run (no Celery / Redis / DB)."""

from __future__ import annotations

import json
from typing import Any, Protocol

from apps.agents.spec import AgentConfigSpec
from apps.runner.backends.memory import MemorySessionBackend, memory_backend_for_turn
from apps.runner.loop import SessionRunner
from apps.runner.spec_loader import (
    build_agent_config_spec,
    load_agent_config_spec,
    load_agent_config_spec_file,
)


def resolve_run_agent_spec(
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    system_prompt: str | None = None,
    spec: str | None = None,
    spec_file: str | None = None,
) -> AgentConfigSpec:
    if spec_file:
        return load_agent_config_spec_file(spec_file)
    if spec:
        return load_agent_config_spec(spec)
    if not provider or not model:
        raise ValueError('Pass --provider and --model, or --spec / --spec-file')
    return build_agent_config_spec(
        provider=provider,
        model=model,
        temperature=temperature,
        system_prompt=system_prompt or 'You are a helpful assistant.',
    )


def run_agent_turn(*, input_text: str, spec: AgentConfigSpec) -> MemorySessionBackend:
    backend = memory_backend_for_turn(spec, input_text=input_text)
    SessionRunner(backend).run()
    return backend


class _TextStream(Protocol):
    def write(self, text: str, /) -> int | None: ...


def write_run_agent_events(backend: MemorySessionBackend, stream: _TextStream) -> None:
    for event in backend.events():
        stream.write(json.dumps(event.to_stream_dict(session_id=backend.session_id), default=str))
        stream.write('\n')


def run_agent_from_options(options: dict[str, Any], *, stream: _TextStream) -> None:
    spec = resolve_run_agent_spec(
        provider=options.get('provider'),
        model=options.get('model'),
        temperature=options.get('temperature'),
        system_prompt=options.get('system_prompt'),
        spec=options.get('spec'),
        spec_file=options.get('spec_file'),
    )
    backend = run_agent_turn(input_text=options['input'], spec=spec)
    write_run_agent_events(backend, stream)
