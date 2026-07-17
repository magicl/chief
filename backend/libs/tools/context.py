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


def _noop_secret_supplier_factory(
    _cred_ref: str | None,
    _cred_type: str,
) -> Callable[[], str | None]:
    """Default factory used when tests construct a ToolContext without credentials."""
    return lambda: None


@dataclass(frozen=True)
class ToolContext:
    """Immutable snapshot of agent/session state available to every tool.

    Tools use this to observe the agent config (e.g. skills list), resolve
    credentials via ``secret_supplier_factory``, and access session identity
    without ad-hoc kwargs.

    ``user_id`` is always set: every agent run has an owning user.
    """

    spec: AgentConfigSpec
    user_id: int
    agent_id: UUID | None = None
    session_id: UUID | None = None
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]] = field(
        default=_noop_secret_supplier_factory,
    )
    client_factories: dict[str, Callable[..., Any]] = field(default_factory=dict)


def token_supplier_for(
    ctx: ToolContext,
    *,
    credential_type: str | None,
    credential_ref: str | None = None,
) -> Callable[[], str | None]:
    """Build a token supplier for tool ``bind``.

    Tools that need secrets declare ``credential_type``. Ingest rejects
    ``credential_ref`` on tools without a credential type, so there is no
    valid ref-without-type case — wiring keys off ``credential_type`` only.
    """
    if credential_type is None:
        return lambda: None
    return ctx.secret_supplier_factory(credential_ref, credential_type)
