# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Provider-neutral tool definitions built from agent config permissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.agents.spec import ToolPermission
from apps.agents.tools.base import qualified_tool_name
from apps.agents.tools.registry import get_tool


@dataclass(frozen=True)
class ToolDefinition:
    """Chief tool schema — providers translate this to their wire format."""

    name: str
    description: str
    parameters: dict[str, Any]


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
