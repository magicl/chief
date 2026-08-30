# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Register built-in trigger block kinds at Django startup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from apps.agents.block_gate import UNEVALUATED_REASON
from libs.tools.context import ToolContext
from libs.tools.readiness import BlockResult
from libs.tools.registry import get_tool

if TYPE_CHECKING:
    from apps.agents.models import Agent, Trigger


def wire_block_kinds() -> None:
    """Register the block kinds Chief ships with; safe to call more than once."""
    from apps.agents.block_gate import register_block_kind

    register_block_kind('tool_ready', evaluate_tool_ready)


def evaluate_tool_ready(agent: Agent, _trigger: Trigger, block: dict[str, Any]) -> BlockResult:
    """Resolve one current-spec tool instance and return its registered readiness."""
    current_config = agent.current_config
    tool_id = block.get('tool')
    if current_config is None or not isinstance(tool_id, str):
        return BlockResult(ready=False, reason=UNEVALUATED_REASON)

    spec = current_config.get_spec()
    instance = next((item for item in spec.tools if item.id == tool_id), None)
    if instance is None:
        return BlockResult(ready=False, reason=UNEVALUATED_REASON)
    tool = get_tool(instance.type)
    if tool is None:
        return BlockResult(ready=False, reason=UNEVALUATED_REASON)

    def secret_supplier_factory(
        credential_ref: str | None,
        credential_type: str,
    ) -> Callable[[], str | None]:
        """Resolve tool credentials lazily using the normal runtime key boundary."""
        from apps.keys.services.queries import make_secret_supplier

        return make_secret_supplier(
            agent.user_id,
            name=credential_ref,
            type=credential_type,
        )

    ctx = ToolContext(
        spec=spec,
        user_id=agent.user_id,
        agent_id=agent.pk,
        secret_supplier_factory=secret_supplier_factory,
        client_factories={},
    )
    return tool.readiness(ctx, instance)
