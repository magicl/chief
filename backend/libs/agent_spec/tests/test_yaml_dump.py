# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for stable agent spec YAML dumping."""

from __future__ import annotations

import yaml
from libs.agent_spec import load_example
from libs.agent_spec.yaml_dump import dump_agent_config_spec

from olib.py.django.test.cases import OTestCase


class TestYamlDump(OTestCase):
    def test_source_adapter_serializes_as_type(self) -> None:
        """Queue sources should use ``type:`` in YAML, not ``adapter_type:``."""
        dumped = dump_agent_config_spec(load_example('gmail-triage'))
        self.assertIn('type: gmail', dumped)
        self.assertNotIn('adapter_type:', dumped)

    def test_dump_collapses_fields_inherited_from_integration(self) -> None:
        """Resolved integration fields should not be re-emitted on tools/sources."""
        dumped = dump_agent_config_spec(load_example('gmail-triage'))
        parsed = yaml.safe_load(dumped)
        tool = parsed['tools'][0]
        self.assertEqual(tool.get('integration'), 'gmail-personal')
        self.assertNotIn('type', tool)
        self.assertNotIn('credential_ref', tool)
        self.assertNotIn('config', tool)
        source = parsed['queues'][0]['sources'][0]
        self.assertEqual(source.get('integration'), 'gmail-personal')
        self.assertNotIn('type', source)
        self.assertNotIn('credential_ref', source)
        self.assertEqual(
            source.get('config', {}).get('query'), 'in:inbox -label:x-act -label:x-read -label:x-spam -label:x-unimp'
        )
        self.assertNotIn('subject', source.get('config', {}))
