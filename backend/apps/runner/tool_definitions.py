# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Build provider tool definitions from agent config permissions."""

from __future__ import annotations

from typing import Any

from apps.agents.spec import ToolPermission
from libs.tools.base import qualified_tool_name
from libs.tools.registry import get_tool
from libs.tools.schema import ToolDefinition


def build_tool_definitions(
    permissions: list[ToolPermission],
    *,
    is_allowed: Any,
) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    for perm in permissions:
        tool = get_tool(perm.tool)
        if tool is None:
            continue
        for fn in tool.functions():
            if not is_allowed(perm.tool, fn.name, permission=perm):
                continue
            definitions.append(
                ToolDefinition(
                    name=qualified_tool_name(perm.tool, fn.name),
                    description=fn.description,
                    parameters=fn.parameters,
                )
            )
    return definitions
