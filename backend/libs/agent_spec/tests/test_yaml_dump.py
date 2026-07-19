# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for stable agent spec YAML dumping."""

from __future__ import annotations

import yaml
from libs.agent_spec import load_example
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.file.yaml_dump import dump_editable_yaml

from olib.py.django.test.cases import OTestCase


class TestDumpEditableYaml(OTestCase):
    """Verify readable formatting without changing YAML values."""

    def test_multiline_string_uses_literal_block(self) -> None:
        """Ordinary newlines should produce an editable literal scalar."""
        dumped = dump_editable_yaml({'notes': 'First line.\nSecond line.'})

        self.assertIn('notes: |-\n  First line.\n  Second line.\n', dumped)
        self.assertEqual(yaml.safe_load(dumped), {'notes': 'First line.\nSecond line.'})

    def test_default_width_matches_safe_dump(self) -> None:
        """Shared dumps should retain PyYAML's prior wrapping width."""
        data = {'notes': 'one two three four five six seven eight nine ten ' * 3}

        dumped = dump_editable_yaml(data)

        self.assertEqual(
            dumped,
            yaml.safe_dump(
                data,
                sort_keys=True,
                default_flow_style=False,
                allow_unicode=True,
                width=80,
            ),
        )

    def test_special_line_breaks_preserve_value(self) -> None:
        """Line-break code points normalized by blocks should remain quoted."""
        data = {'notes': 'First line.\nNext\x85line.'}

        dumped = dump_editable_yaml(data)

        self.assertNotIn('notes: |', dumped)
        self.assertEqual(yaml.safe_load(dumped), data)


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
