# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for config editor mutations."""

from apps.agents.services.config_mutations import (
    ConfigMutationError,
    apply_config_mutation,
)
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

    def test_multiline_system_prompt_uses_literal_block(self) -> None:
        """Helper-inserted multiline text should remain easy to edit as YAML."""
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {
                'action': 'set_system_prompt',
                'system_prompt': 'First line.\nSecond line.',
            },
        )
        self.assertIn('system_prompt: |-\n  First line.\n  Second line.\n', updated)

    def test_add_button_trigger_includes_button_text(self) -> None:
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {
                'action': 'add_trigger',
                'name': 'triage',
                'kind': 'button',
                'button_text': 'Triage inbox',
                'prompt': 'Triage the inbox now.',
            },
        )
        self.assertIn('kind: button', updated)
        self.assertIn('button_text: Triage inbox', updated)
        self.assertIn('prompt: Triage the inbox now.', updated)

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

    def test_mutation_accepts_valueless_integrations_key(self) -> None:
        """Editors often leave a bare ``integrations:`` behind; it must validate as empty."""
        raw = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Just take instructions
integrations:
tools: []
triggers:
  - name: manual
    kind: manual
"""
        updated = apply_config_mutation(
            raw,
            {'action': 'add_tool', 'id': 'clock', 'type': 'clock', 'allow': ['now']},
        )
        self.assertIn('id: clock', updated)


VALUELESS_COLLECTIONS_YAML = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Just take instructions
tools:
triggers:
queues:
"""


class ValuelessCollectionMutationTests(OTestCase):
    """Helpers must insert into and read from a collection whose YAML key has no value."""

    def test_add_tool_into_valueless_tools(self) -> None:
        updated = apply_config_mutation(
            VALUELESS_COLLECTIONS_YAML,
            {'action': 'add_tool', 'id': 'clock', 'type': 'clock', 'allow': ['now']},
        )
        self.assertIn('id: clock', updated)

    def test_add_trigger_into_valueless_triggers(self) -> None:
        updated = apply_config_mutation(
            VALUELESS_COLLECTIONS_YAML,
            {'action': 'add_trigger', 'name': 'manual', 'kind': 'manual'},
        )
        self.assertIn('name: manual', updated)

    def test_add_queue_into_valueless_queues(self) -> None:
        updated = apply_config_mutation(
            VALUELESS_COLLECTIONS_YAML,
            {'action': 'add_queue', 'id': 'inbox'},
        )
        self.assertIn('id: inbox', updated)

    def test_add_source_into_valueless_sources(self) -> None:
        raw = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Just take instructions
triggers:
  - name: manual
    kind: manual
queues:
  - id: inbox
    sources:
"""
        updated = apply_config_mutation(
            raw,
            {'action': 'add_source', 'queue_id': 'inbox', 'id': 'gmail-a', 'type': 'test'},
        )
        self.assertIn('id: gmail-a', updated)

    def test_remove_tool_from_valueless_tools_reports_unknown_id(self) -> None:
        with self.assertRaises(ConfigMutationError):
            apply_config_mutation(
                VALUELESS_COLLECTIONS_YAML,
                {'action': 'remove_tool', 'id': 'clock'},
            )

    def test_remove_queue_from_valueless_queues_reports_unknown_id(self) -> None:
        with self.assertRaises(ConfigMutationError):
            apply_config_mutation(
                VALUELESS_COLLECTIONS_YAML,
                {'action': 'remove_queue', 'id': 'inbox'},
            )
