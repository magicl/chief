# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Build provider tool definitions from agent config permissions."""

from __future__ import annotations

from collections.abc import Callable

from apps.agents.spec import ToolInstance
from libs.tools.base import wire_tool_name
from libs.tools.registry import get_tool
from libs.tools.schema import ToolDefinition


def build_tool_definitions(
    instances: list[ToolInstance],
    *,
    is_allowed: Callable[..., bool],
) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    for inst in instances:
        tool = get_tool(inst.type)
        if tool is None:
            continue
        for fn in tool.functions():
            if not is_allowed(inst.id, fn.name, instance=inst):
                continue
            definitions.append(
                ToolDefinition(
                    name=wire_tool_name(inst.id, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    return definitions
