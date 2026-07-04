# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bind tool registry instances to per-user credential suppliers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from apps.agents.spec import ToolInstance
from apps.keys.services.queries import make_secret_supplier
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
) -> Callable[[str, dict[str, Any]], Any]:
    bind = getattr(tool, 'bind', None)
    if bind is not None and token_supplier is not None:
        return cast(
            Callable[[str, dict[str, Any]], Any],
            bind(token_supplier=token_supplier),
        )
    return tool.invoke


def build_bound_tools(
    instances: list[ToolInstance],
    *,
    user_id: int | None,
) -> dict[str, BoundToolInstance]:
    bound: dict[str, BoundToolInstance] = {}
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            raise ValueError(f'Unknown tool type {inst.type!r}')
        supplier = None
        cred_type = getattr(tool, 'credential_type', None)
        if cred_type and user_id is not None:
            supplier = make_secret_supplier(user_id, name=inst.credential_ref, type=cred_type)
        invoke = bind_tool_invoke(tool, token_supplier=supplier)
        bound[inst.id] = BoundToolInstance(
            instance_id=inst.id,
            tool_type=inst.type,
            invoke=invoke,
        )
    return bound
