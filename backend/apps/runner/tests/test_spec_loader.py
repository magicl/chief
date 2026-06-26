# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import textwrap

from apps.runner.spec_loader import build_agent_config_spec, load_agent_config_spec

from olib.py.django.test.cases import OTestCase


class TestSpecLoader(OTestCase):
    def test_load_json_spec(self) -> None:
        raw = textwrap.dedent("""
            {
              "llm": {"provider": "openai", "model": "gpt-4o-mini"},
              "system_prompt": "hello",
              "tools": []
            }
            """).strip()
        spec = load_agent_config_spec(raw)
        self.assertEqual(spec.llm.provider, 'openai')
        self.assertEqual(spec.system_prompt, 'hello')

    def test_load_yaml_spec(self) -> None:
        raw = textwrap.dedent("""
            llm:
              provider: anthropic
              model: claude-3-5-haiku-20241022
            system_prompt: yaml prompt
            tools: []
            """).strip()
        spec = load_agent_config_spec(raw)
        self.assertEqual(spec.llm.provider, 'anthropic')
        self.assertEqual(spec.system_prompt, 'yaml prompt')

    def test_build_minimal_spec(self) -> None:
        spec = build_agent_config_spec(provider='openai', model='gpt-4o-mini')
        self.assertEqual(spec.llm.model, 'gpt-4o-mini')
        self.assertEqual(spec.tools[0].tool, 'clock')
