# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for parsing local agent YAML envelopes."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp

from apps.agents.services.config_validation import ConfigValidationError
from apps.local_disk.agent_parse import parse_agent_file

from olib.py.django.test.cases import OTestCase


class TestAgentParse(OTestCase):
    def setUp(self) -> None:
        """Create an isolated local root for parser fixtures."""
        super().setUp()
        self.root = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.agents_path = self.root / 'agents'
        self.agents_path.mkdir()

    def write_agent(self, body: str, filename: str = 'daily-helper.yaml') -> Path:
        """Write an agent fixture and return its path."""
        path = self.agents_path / filename
        path.write_text(body, encoding='utf-8')
        return path

    def test_defaults_identifier_and_name_from_stem(self) -> None:
        path = self.write_agent(
            """owner: alice
schema_version: 2
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: Help daily.
triggers: []
tools: []
queues: []
""",
        )

        parsed = parse_agent_file(path, root=self.root)

        self.assertEqual(parsed.owner, 'alice')
        self.assertEqual(parsed.identifier, 'daily-helper')
        self.assertEqual(parsed.name, 'daily-helper')
        self.assertEqual(parsed.source_path, 'agents/daily-helper.yaml')
        self.assertTrue(parsed.source_rev.startswith('sha256:'))

    def test_strips_envelope_before_spec_validation(self) -> None:
        path = self.write_agent(
            """owner: alice
identifier: inbox-agent
name: Inbox Agent
schema_version: 2
description: Triage inbox messages.
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: Triage mail.
triggers: []
tools: []
queues: []
""",
        )

        parsed = parse_agent_file(path, root=self.root)

        self.assertEqual(parsed.identifier, 'inbox-agent')
        self.assertEqual(parsed.name, 'Inbox Agent')
        self.assertEqual(parsed.spec.description, 'Triage inbox messages.')
        self.assertNotIn('owner:', parsed.body_yaml)
        self.assertNotIn('identifier:', parsed.body_yaml)
        self.assertNotIn('name:', parsed.body_yaml)

    def test_rejects_missing_owner(self) -> None:
        path = self.write_agent(
            """schema_version: 2
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: Help.
""",
        )

        with self.assertRaises(ValueError):
            parse_agent_file(path, root=self.root)

    def test_rejects_body_that_does_not_match_agent_spec(self) -> None:
        path = self.write_agent(
            """owner: alice
schema_version: 2
system_prompt: Missing an LLM.
""",
        )

        with self.assertRaises(ConfigValidationError):
            parse_agent_file(path, root=self.root)
