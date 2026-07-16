# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bind tool registry instances to per-user credential suppliers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from libs.agent_spec import ToolInstance
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools, get_tool


@dataclass(frozen=True)
class BoundToolInstance:
    instance_id: str
    tool_type: str
    invoke: Callable[[str, dict[str, Any]], Any]
    is_auto: bool = False


def build_bound_tools(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
) -> dict[str, BoundToolInstance]:
    """Map tool instance ids to invoke callables with context wired.

    Processes explicit tools from ``instances`` (config tools[]), then scans
    the registry for auto-tools whose ``should_include(ctx)`` returns True.
    """
    bound: dict[str, BoundToolInstance] = {}
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            raise ValueError(f'Unknown tool type {inst.type!r}')
        invoke = tool.bind(ctx, inst) or tool.invoke
        bound[inst.id] = BoundToolInstance(
            instance_id=inst.id,
            tool_type=inst.type,
            invoke=invoke,
        )
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        invoke = tool.bind(ctx) or tool.invoke
        bound[tool.name] = BoundToolInstance(
            instance_id=tool.name,
            tool_type=tool.name,
            invoke=invoke,
            is_auto=True,
        )
    return bound
