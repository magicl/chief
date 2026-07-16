# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Build provider tool definitions from agent config permissions."""

from __future__ import annotations

from collections.abc import Callable

from libs.agent_spec import ToolInstance
from libs.tools.base import wire_tool_name
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools, get_tool
from libs.tools.schema import ToolDefinition


def build_tool_definitions(
    instances: list[ToolInstance],
    *,
    ctx: ToolContext,
    is_allowed: Callable[..., bool],
) -> list[ToolDefinition]:
    """Build LLM tool definitions for explicit and auto-tools.

    Explicit tools are gated by ``is_allowed``; auto-tools bypass permission
    checks (they are platform-managed and have no user-configurable allow/deny).
    """
    definitions: list[ToolDefinition] = []
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            continue
        for fn in tool.functions(ctx, inst):
            if not is_allowed(inst.id, fn.name, instance=inst):
                continue
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(inst.id, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    for tool in all_tools().values():
        if not tool.auto or not tool.should_include(ctx):
            continue
        for fn in tool.functions(ctx):
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(tool.name, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    return definitions
