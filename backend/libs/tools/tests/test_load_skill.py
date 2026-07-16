# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from libs.agent_spec import AgentConfigSpec, LLMSpec
from libs.agent_spec.spec import SkillSpec
from libs.tools.context import ToolContext
from libs.tools.tools.load_skill import LoadSkillTool

from olib.py.django.test.cases import OTestCase


def _make_ctx(skills: list[SkillSpec] | None = None) -> ToolContext:
    """Build a ToolContext with optional skills."""
    spec = AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='test',
        skills=skills or [],
    )
    return ToolContext(spec=spec)


class TestLoadSkillTool(OTestCase):
    def setUp(self) -> None:
        self.tool = LoadSkillTool()

    def test_should_include_false_when_no_skills(self) -> None:
        ctx = _make_ctx()
        self.assertFalse(self.tool.should_include(ctx))

    def test_should_include_true_when_skills_present(self) -> None:
        ctx = _make_ctx(skills=[SkillSpec(id='a', description='desc', content='body')])
        self.assertTrue(self.tool.should_include(ctx))

    def test_functions_embed_skill_names_in_description(self) -> None:
        ctx = _make_ctx(
            skills=[
                SkillSpec(id='triage', description='Email triage', content='...'),
                SkillSpec(id='style', description='Writing style', content='...'),
            ]
        )
        fns = self.tool.functions(ctx)
        self.assertEqual(len(fns), 1)
        self.assertIn('triage: Email triage', fns[0].description)
        self.assertIn('style: Writing style', fns[0].description)

    def test_bind_returns_content_for_valid_skill(self) -> None:
        ctx = _make_ctx(
            skills=[
                SkillSpec(id='triage', description='Email triage', content='Classify by urgency'),
            ]
        )
        invoke = self.tool.bind(ctx)
        assert invoke is not None
        result = invoke('load', {'name': 'triage'})
        self.assertEqual(result, {'skill': 'triage', 'content': 'Classify by urgency'})

    def test_bind_returns_not_found_for_unknown_skill(self) -> None:
        ctx = _make_ctx(
            skills=[
                SkillSpec(id='triage', description='d', content='c'),
            ]
        )
        invoke = self.tool.bind(ctx)
        assert invoke is not None
        result = invoke('load', {'name': 'nonexistent'})
        self.assertIn('error', result)
        self.assertIn('nonexistent', result['error'])

    def test_bind_returns_not_found_for_unknown_function(self) -> None:
        ctx = _make_ctx(skills=[SkillSpec(id='a', description='d', content='c')])
        invoke = self.tool.bind(ctx)
        assert invoke is not None
        result = invoke('bad_func', {})
        self.assertIn('error', result)

    def test_auto_flag_is_true(self) -> None:
        self.assertTrue(self.tool.auto)
