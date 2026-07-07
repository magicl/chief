# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for YAML round-trip helpers."""

from __future__ import annotations

from libs.agent_spec.yaml_roundtrip import dump_yaml_document, load_yaml_document

from olib.py.django.test.cases import OTestCase


class TestYamlRoundtrip(OTestCase):
    def test_preserves_hash_comments(self) -> None:
        raw = """# Agent notes
schema_version: 2
# LLM block
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Hello
tools: []
triggers: []
queues: []
"""
        doc = load_yaml_document(raw)
        out = dump_yaml_document(doc)
        self.assertIn('# Agent notes', out)
        self.assertIn('# LLM block', out)
