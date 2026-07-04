# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bind tool registry instances to per-user credential suppliers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from apps.keys.services.queries import make_secret_supplier

# isort: split

from libs.agent_spec import ToolInstance
from libs.tools.base import Tool
from libs.tools.registry import get_tool


@dataclass(frozen=True)
class BoundToolInstance:
    instance_id: str
    tool_type: str
    invoke: Callable[[str, dict[str, Any]], Any]


def bind_tool_invoke(
    tool: Tool,
    *,
    token_supplier: Callable[[], str | None] | None,
    user_id: int | None = None,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
) -> Callable[[str, dict[str, Any]], Any]:
    """Return a bound invoke for *tool*, injecting credentials or queue session context."""
    bind = getattr(tool, 'bind', None)
    if bind is not None:
        if tool.name == 'queue':
            return cast(
                Callable[[str, dict[str, Any]], Any],
                bind(user_id=user_id, agent_id=agent_id, session_id=session_id),
            )
        if token_supplier is not None:
            return cast(
                Callable[[str, dict[str, Any]], Any],
                bind(token_supplier=token_supplier),
            )
    return tool.invoke


def build_bound_tools(
    instances: list[ToolInstance],
    *,
    user_id: int | None,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
) -> dict[str, BoundToolInstance]:
    """Map tool instance ids to invoke callables with credentials and queue context wired."""
    bound: dict[str, BoundToolInstance] = {}
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            raise ValueError(f'Unknown tool type {inst.type!r}')
        supplier = None
        cred_type = getattr(tool, 'credential_type', None)
        if cred_type and user_id is not None:
            supplier = make_secret_supplier(user_id, name=inst.credential_ref, type=cred_type)
        invoke = bind_tool_invoke(
            tool,
            token_supplier=supplier,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        bound[inst.id] = BoundToolInstance(
            instance_id=inst.id,
            tool_type=inst.type,
            invoke=invoke,
        )
    return bound
