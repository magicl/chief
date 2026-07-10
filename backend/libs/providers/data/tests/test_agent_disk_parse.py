# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from pathlib import Path
from tempfile import TemporaryDirectory

from libs.providers.data.agent_disk_parse import AgentDiskFile, parse_agent_file
from yaml import YAMLError

from olib.py.django.test.cases import OTestCase


class TestAgentDiskParse(OTestCase):
    """Verify Django-free parsing of agent data files."""

    def test_parse_defaults_envelope_and_sets_provenance(self) -> None:
        """Default identifier and name from the filename stem."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'daily-helper.yaml'
            path.parent.mkdir()
            path.write_text(
                'owner: alice\nschema_version: 2\nsystem_prompt: Help daily.\n',
                encoding='utf-8',
            )

            parsed = parse_agent_file(path, root=root)

        self.assertEqual(
            parsed,
            AgentDiskFile(
                owner='alice',
                identifier='daily-helper',
                name='daily-helper',
                body={'schema_version': 2, 'system_prompt': 'Help daily.'},
                body_yaml='schema_version: 2\nsystem_prompt: Help daily.\n',
                source_path='agents/daily-helper.yaml',
                source_rev=parsed.source_rev,
            ),
        )
        self.assertTrue(parsed.source_rev.startswith('sha256:'))

    def test_parse_strips_explicit_envelope_from_body(self) -> None:
        """Keep explicit envelope values out of the returned config body."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'file.yaml'
            path.parent.mkdir()
            path.write_text(
                'owner: alice\nidentifier: inbox-agent\nname: Inbox Agent\n'
                'schema_version: 2\ndescription: Triage inbox messages.\n',
                encoding='utf-8',
            )

            parsed = parse_agent_file(path, root=root)

        self.assertEqual(parsed.identifier, 'inbox-agent')
        self.assertEqual(parsed.name, 'Inbox Agent')
        self.assertEqual(
            parsed.body,
            {'schema_version': 2, 'description': 'Triage inbox messages.'},
        )
        self.assertNotIn('owner:', parsed.body_yaml)
        self.assertNotIn('identifier:', parsed.body_yaml)
        self.assertNotIn('name:', parsed.body_yaml)

    def test_parse_requires_owner(self) -> None:
        """Reject files without the required owner envelope field."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('schema_version: 2\n', encoding='utf-8')

            with self.assertRaises(ValueError):
                parse_agent_file(path, root=root)

    def test_parse_leaves_config_validation_to_app(self) -> None:
        """Return incomplete config bodies for app-layer validation."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('owner: alice\nsystem_prompt: Missing an LLM.\n', encoding='utf-8')

            parsed = parse_agent_file(path, root=root)

        self.assertEqual(parsed.body, {'system_prompt': 'Missing an LLM.'})

    def test_parse_rejects_non_mapping_yaml(self) -> None:
        """Reject agent files whose YAML root is not a mapping."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('- not\n- a mapping\n', encoding='utf-8')

            with self.assertRaises(YAMLError):
                parse_agent_file(path, root=root)

    def test_envelope_only_edit_keeps_source_rev(self) -> None:
        """Hash only the config body so envelope-only edits do not bump revisions."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'agents' / 'file.yaml'
            path.parent.mkdir()
            path.write_text(
                'owner: alice\nidentifier: inbox\nname: Inbox\nschema_version: 2\nsystem_prompt: Help.\n',
                encoding='utf-8',
            )
            first = parse_agent_file(path, root=root)
            path.write_text(
                'owner: bob\nidentifier: inbox\nname: Renamed\nschema_version: 2\nsystem_prompt: Help.\n',
                encoding='utf-8',
            )
            second = parse_agent_file(path, root=root)

        self.assertEqual(first.body_yaml, second.body_yaml)
        self.assertEqual(first.source_rev, second.source_rev)
        self.assertEqual(second.owner, 'bob')
        self.assertEqual(second.name, 'Renamed')
