# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for config editor mutations."""

from apps.agents.services.config_mutations import apply_config_mutation
from libs.agent_spec import load_example
from libs.agent_spec.yaml_dump import dump_agent_config_spec

from olib.py.django.test.cases import OTestCase


class ConfigMutationTests(OTestCase):
    def test_add_tool_instance(self) -> None:
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {'action': 'add_tool', 'id': 'queue', 'type': 'queue', 'allow': ['take']},
        )
        self.assertIn('id: queue', updated)
        self.assertIn('type: queue', updated)

    def test_add_schedule_trigger_includes_prompt(self) -> None:
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {
                'action': 'add_trigger',
                'name': 'sweep',
                'kind': 'schedule',
                'cron': '0 * * * *',
                'prompt': 'Run the hourly sweep.',
            },
        )
        self.assertIn('name: sweep', updated)
        self.assertIn('prompt: Run the hourly sweep.', updated)

    def test_add_schedule_trigger_uses_default_prompt_when_omitted(self) -> None:
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {
                'action': 'add_trigger',
                'name': 'sweep',
                'kind': 'schedule',
                'cron': '0 * * * *',
            },
        )
        self.assertIn('Scheduled run started. Execute your configured tasks.', updated)

    def test_mutation_preserves_existing_comments(self) -> None:
        raw = """# keep me
schema_version: 2
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Clock.
tools: []
triggers: []
queues: []
"""
        updated = apply_config_mutation(
            raw,
            {'action': 'add_tool', 'id': 'queue', 'type': 'queue', 'allow': ['take']},
        )
        self.assertIn('# keep me', updated)
        self.assertIn('id: queue', updated)
