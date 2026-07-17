# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Load-skill auto-tool: lets agents discover and load named prompt blocks on demand."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance


class LoadSkillTool(Tool):
    """Auto-tool that activates when the agent has skills configured.

    Embeds skill names and descriptions in the tool definition so the LLM sees
    them without making a call. ``load`` returns full content for one skill.
    """

    name = 'load_skill'
    auto = True

    def should_include(self, ctx: ToolContext) -> bool:
        """Include only when the agent config declares at least one skill."""
        return len(ctx.spec.skills) > 0

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """Build a single ``load`` function with the skill catalog in its description."""
        skill_list = '\n'.join(f'- {s.id}: {s.description}' for s in ctx.spec.skills)
        return [
            ToolFunction(
                name='load',
                description=(
                    f'Load a skill by name to get detailed instructions. '
                    f'Available skills:\n{skill_list}\n\n'
                    'You SHOULD call this tool whenever the current task or context '
                    'relates to one of the listed skills. Load the skill BEFORE '
                    'acting on the topic it covers.'
                ),
                parameters={
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Skill id to load.'},
                    },
                    'required': ['name'],
                },
                handler=self._unbound,
            ),
        ]

    def bind(self, ctx: ToolContext, instance: ToolInstance | None = None) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke that looks up skill content by id from the frozen spec."""
        skills_by_id = {s.id: s.content for s in ctx.spec.skills}

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'load':
                return {'error': f'Unknown function {function!r}'}
            name = arguments.get('name', '')
            content = skills_by_id.get(name)
            if content is None:
                available = ', '.join(skills_by_id.keys())
                return {'error': f'Unknown skill {name!r}. Available: {available}'}
            return {'skill': name, 'content': content}

        return invoke

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('load_skill requires bind')
