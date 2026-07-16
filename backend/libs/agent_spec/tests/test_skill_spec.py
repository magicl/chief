# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from typing import Any

from libs.agent_spec import AgentConfigSpec, LLMSpec
from libs.agent_spec.spec import SkillSpec
from pydantic import ValidationError

from olib.py.django.test.cases import OTestCase


class TestSkillSpec(OTestCase):
    def _base_spec(self, **overrides: Any) -> AgentConfigSpec:
        """Build a minimal spec with optional overrides."""
        return AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='test',
            **overrides,
        )

    def test_skills_default_empty(self) -> None:
        spec = self._base_spec()
        self.assertEqual(spec.skills, [])

    def test_valid_skill(self) -> None:
        spec = self._base_spec(
            skills=[
                SkillSpec(id='triage', description='Email triage rules', content='Classify emails...'),
            ]
        )
        self.assertEqual(len(spec.skills), 1)
        self.assertEqual(spec.skills[0].id, 'triage')

    def test_duplicate_skill_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base_spec(
                skills=[
                    SkillSpec(id='triage', description='A', content='X'),
                    SkillSpec(id='triage', description='B', content='Y'),
                ]
            )

    def test_empty_description_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(id='x', description='', content='Y')

    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(id='x', description='Y', content='')

    def test_invalid_skill_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSpec(id='Bad-Id', description='Y', content='Z')
