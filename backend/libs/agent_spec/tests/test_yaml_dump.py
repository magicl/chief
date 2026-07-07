# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for stable agent spec YAML dumping."""

from __future__ import annotations

from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class TestYamlDump(OTestCase):
    def test_source_adapter_serializes_as_type(self) -> None:
        """Queue sources should use ``type:`` in YAML, not ``adapter_type:``."""
        dumped = dump_agent_config_spec(load_example('gmail-triage'))
        self.assertIn('type: gmail', dumped)
        self.assertNotIn('adapter_type:', dumped)
