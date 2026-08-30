# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for config editor mutations."""

from apps.agents.services.config_mutations import (
    ConfigMutationError,
    _tool_readiness_blocks,
    apply_config_mutation,
)
from libs.agent_spec import load_example
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_spec.yaml_roundtrip import load_yaml_document

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


READINESS_TOOLS_YAML = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Process work.
tools:
  - id: first-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: first
      roots: [Journal]
  - id: clock
    type: clock
    allow: [now]
  - id: second-vault
    type: obsidian
    credential_ref: obsidian-sync
    config:
      vault: second
      roots: [Notes]
triggers:
  - name: manual
    kind: manual
queues:
  - id: inbox
"""

ALWAYS_READY_TOOLS_YAML = """schema_version: 4
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Process work.
tools:
  - id: clock
    type: clock
    allow: [now]
triggers:
  - name: manual
    kind: manual
queues:
  - id: inbox
"""


class TriggerReadinessBlockTests(OTestCase):
    """Add trigger helpers gate queue/schedule sessions on readiness-reporting tools."""

    def test_add_schedule_trigger_gates_on_readiness_tools_in_document_order(self) -> None:
        """Schedule helpers gate on each readiness-reporting tool in YAML order."""
        updated = apply_config_mutation(
            READINESS_TOOLS_YAML,
            {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
        )
        trigger = load_yaml_document(updated)['triggers'][-1]
        self.assertEqual(
            [dict(block) for block in trigger['blocks']],
            [
                {'kind': 'tool_ready', 'tool': 'first-vault'},
                {'kind': 'tool_ready', 'tool': 'second-vault'},
            ],
        )

    def test_add_queue_trigger_gates_on_readiness_tools(self) -> None:
        """Queue helpers gate on readiness-reporting tools already in the YAML."""
        updated = apply_config_mutation(
            READINESS_TOOLS_YAML,
            {'action': 'add_trigger', 'name': 'worker', 'kind': 'queue', 'queue': 'inbox'},
        )
        trigger = load_yaml_document(updated)['triggers'][-1]
        self.assertEqual(
            [dict(block) for block in trigger['blocks']],
            [
                {'kind': 'tool_ready', 'tool': 'first-vault'},
                {'kind': 'tool_ready', 'tool': 'second-vault'},
            ],
        )

    def test_add_trigger_with_only_always_ready_tools_omits_blocks(self) -> None:
        """Always-ready tools do not add an empty or ineffective block list."""
        updated = apply_config_mutation(
            ALWAYS_READY_TOOLS_YAML,
            {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
        )
        self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])

    def test_manual_button_and_agent_triggers_omit_readiness_blocks(self) -> None:
        """Manual, button, and agent helper triggers remain ungated even when readiness tools exist."""
        for mutation in (
            {'action': 'add_trigger', 'name': 'manual-two', 'kind': 'manual'},
            {
                'action': 'add_trigger',
                'name': 'button',
                'kind': 'button',
                'button_text': 'Run',
                'prompt': 'Run now.',
            },
            {
                'action': 'add_trigger',
                'name': 'child',
                'kind': 'agent',
                'prompt': 'Run this child task.',
            },
        ):
            with self.subTest(kind=mutation['kind']):
                updated = apply_config_mutation(READINESS_TOOLS_YAML, mutation)
                self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])

    def test_add_schedule_trigger_with_valueless_tools_omits_blocks(self) -> None:
        """A valueless tools key behaves like an empty list during readiness scanning."""
        updated = apply_config_mutation(
            VALUELESS_COLLECTIONS_YAML,
            {'action': 'add_trigger', 'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
        )
        self.assertNotIn('blocks', load_yaml_document(updated)['triggers'][-1])

    def test_readiness_block_scan_skips_rows_the_spec_would_reject(self) -> None:
        """Rows that never reach the validator (non-mapping, partial, unknown type) are skipped.

        Exercised directly because such a document cannot survive whole-spec validation,
        yet the scan runs before that check and must not raise on half-typed YAML.
        """
        doc = load_yaml_document(
            """tools:
  - just-a-string
  - id: no-type
  - type: obsidian
  - id: unknown-tool
    type: not-registered
  - id: nested-type
    type:
      nested: mapping
  - id:
      not: a-string
    type: obsidian
  - id: list-type
    type: [obsidian]
  - id: real-vault
    type: obsidian
""",
        )
        blocks = _tool_readiness_blocks(doc)
        self.assertEqual([dict(block) for block in blocks], [{'kind': 'tool_ready', 'tool': 'real-vault'}])
