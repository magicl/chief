# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-code tool registry base types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

from libs.tools.context import ToolContext


@dataclass(frozen=True)
class ToolFunction:
    """One callable sub-function exposed by a tool namespace."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    readonly: bool = False


def qualified_tool_name(tool_name: str, function_name: str) -> str:
    return f'{tool_name}.{function_name}'


def wire_tool_name(tool_name: str, function_name: str | None = None) -> str:
    """Provider-safe tool name (``^[a-zA-Z0-9_-]+$``).

    With two args: ``wire_tool_name('clock-a', 'now')`` → ``clock-a__now``.
    With one arg: maps ``clock.now`` or ``clock__now`` to the wire form.
    """
    if function_name is not None:
        return f'{tool_name}__{function_name}'
    tool, fn = parse_qualified_tool_name(tool_name)
    return f'{tool}__{fn}'


def parse_qualified_tool_name(name: str) -> tuple[str, str]:
    if '.' in name:
        tool, fn = name.split('.', 1)
        return tool, fn
    if '__' in name:
        tool, fn = name.split('__', 1)
        return tool, fn
    return name, 'default'


def qualified_tool_name_from_wire(wire_name: str) -> str:
    tool, fn = parse_qualified_tool_name(wire_name)
    return qualified_tool_name(tool, fn)


class Tool(ABC):
    """A tool namespace (e.g. ``clock``) with one or more sub-functions."""

    name: str
    credential_type: str | None = None
    auto: bool = False

    @abstractmethod
    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        raise NotImplementedError

    def bind(
        self, ctx: ToolContext, instance: ToolInstance | None = None
    ) -> Callable[[str, dict[str, Any]], Any] | None:
        """Return a bound invoke callable, or None to fall back to ``invoke``."""
        return None

    def should_include(self, ctx: ToolContext) -> bool:
        """Whether this auto-tool should appear for the given context."""
        return True

    def invoke(self, function: str, arguments: dict[str, Any]) -> Any:
        """Default function dispatch. Used when bind() returns None."""
        from libs.agent_spec.spec import AgentConfigSpec, LLMSpec

        dummy_ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=0,
        )
        for fn in self.functions(dummy_ctx):
            if fn.name == function:
                return fn.handler(**arguments)
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')
