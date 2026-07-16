# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.runner.tool_definitions import build_tool_definitions
from libs.agent_spec import AgentConfigSpec, LLMSpec, SkillSpec, ToolInstance
from libs.tools.context import ToolContext

from olib.py.django.test.cases import OTestCase


def _make_ctx(spec: AgentConfigSpec | None = None) -> ToolContext:
    """Build a ToolContext with a minimal spec for definition tests."""
    if spec is None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='test',
        )
    return ToolContext(spec=spec)


class TestBuildToolDefinitions(OTestCase):
    def test_two_instances_same_type_get_distinct_wire_names(self) -> None:
        instances = [
            ToolInstance(id='clock-a', type='clock', allow=['now']),
            ToolInstance(id='clock-b', type='clock', allow=['now']),
        ]
        ctx = _make_ctx()
        defs = build_tool_definitions(instances, ctx=ctx, is_allowed=lambda *_a, **_k: True)
        names = {d.name for d in defs}
        self.assertEqual(names, {'clock-a__now', 'clock-b__now'})

    def test_auto_tool_definitions_appear_when_skills_present(self) -> None:
        """load_skill auto-tool definitions are included when the spec has skills."""
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='test',
            skills=[SkillSpec(id='s1', description='Skill one', content='Content')],
        )
        ctx = _make_ctx(spec=spec)
        defs = build_tool_definitions([], ctx=ctx, is_allowed=lambda *_a, **_k: True)
        names = {d.name for d in defs}
        self.assertIn('load_skill__load', names)
        load_def = next(d for d in defs if d.name == 'load_skill__load')
        self.assertIn('s1: Skill one', load_def.description)

    def test_auto_tool_definitions_absent_when_no_skills(self) -> None:
        """load_skill auto-tool definitions are absent when the spec has no skills."""
        ctx = _make_ctx()
        defs = build_tool_definitions([], ctx=ctx, is_allowed=lambda *_a, **_k: True)
        names = {d.name for d in defs}
        self.assertNotIn('load_skill__load', names)
