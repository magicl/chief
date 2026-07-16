# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent/session context passed to tools at wiring and definition time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from libs.agent_spec.spec import AgentConfigSpec


@dataclass(frozen=True)
class ToolContext:
    """Immutable snapshot of agent/session state available to every tool.

    Tools use this to observe the agent config (e.g. skills list), resolve
    credentials via ``secret_supplier_factory``, and access session identity
    without ad-hoc kwargs.
    """

    spec: AgentConfigSpec
    user_id: int | None = None
    agent_id: UUID | None = None
    session_id: UUID | None = None
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]] | None = None
    client_factories: dict[str, Callable[..., Any]] = field(default_factory=dict)
