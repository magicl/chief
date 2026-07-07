# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for persisting editor YAML text on AgentConfig."""

from __future__ import annotations

from apps.agents.ingest import create_agent_from_spec
from apps.agents.services.config_commands import create_from_yaml
from apps.agents.services.config_validation import validate_agent_config_yaml
from django.contrib.auth import get_user_model
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTransactionTestCase


class TestAgentConfigYamlPersistence(OTransactionTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='yaml-user', password='test')

    def test_save_stores_raw_yaml_with_comments(self) -> None:
        raw = """# inbox triage notes
schema_version: 2
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Triage mail.
tools: []
triggers: []
queues: []
"""
        spec = validate_agent_config_yaml(raw)
        agent = create_agent_from_spec(
            self.user,
            spec,
            name='Yaml agent',
            identifier='yaml-agent',
            raw_yaml=raw,
        )
        config = agent.current_config
        assert config is not None
        self.assertIn('# inbox triage notes', config.spec_yaml)
        self.assertEqual(config.display_yaml(), raw)

    def test_create_from_yaml_preserves_comments(self) -> None:
        raw = """# created from paste
schema_version: 2
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Hi
tools: []
triggers: []
queues: []
"""
        agent = create_from_yaml(self.user, raw, name='Pasted')
        config = agent.current_config
        assert config is not None
        self.assertIn('# created from paste', config.spec_yaml)

    def test_legacy_config_without_spec_yaml_falls_back_to_dump(self) -> None:
        spec = load_example('minimal')
        agent = create_agent_from_spec(self.user, spec, name='Legacy', identifier='legacy-yaml')
        config = agent.current_config
        assert config is not None
        config.spec_yaml = ''
        config.save(update_fields=['spec_yaml'])
        self.assertIn('schema_version:', config.display_yaml())
