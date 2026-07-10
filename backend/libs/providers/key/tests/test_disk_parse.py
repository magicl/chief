# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from pathlib import Path
from tempfile import TemporaryDirectory

from libs.providers.key.disk_parse import KeyDiskFile, parse_key_file
from yaml import YAMLError

from olib.py.django.test.cases import OTestCase


class TestKeyDiskParse(OTestCase):
    """Verify Django-free parsing of credential files."""

    def test_parse_defaults_name_and_sets_provenance(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work-openai.yaml'
            path.parent.mkdir()
            path.write_text('type: openai\nowner: alice\nvalue: sk-test\n', encoding='utf-8')

            parsed = parse_key_file(path, root=root)

        self.assertEqual(
            parsed,
            KeyDiskFile(
                name='work-openai',
                type='openai',
                owner='alice',
                value='sk-test',
                source_path='keys/work-openai.yaml',
                source_rev=parsed.source_rev,
            ),
        )
        self.assertTrue(parsed.source_rev.startswith('sha256:'))

    def test_parse_uses_explicit_name(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text(
                'name: personal-openai\ntype: openai\nowner: alice\nvalue: sk-test\n',
                encoding='utf-8',
            )

            parsed = parse_key_file(path, root=root)

        self.assertEqual(parsed.name, 'personal-openai')

    def test_parse_requires_each_mandatory_field(self) -> None:
        for missing in ('type', 'owner', 'value'):
            with self.subTest(missing=missing), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'file.yaml'
                path.parent.mkdir()
                fields = {'type': 'openai', 'owner': 'alice', 'value': 'sk-test'}
                del fields[missing]
                path.write_text(
                    ''.join(f'{key}: {value}\n' for key, value in fields.items()),
                    encoding='utf-8',
                )

                with self.assertRaises(ValueError):
                    parse_key_file(path, root=root)

    def test_parse_returns_unknown_type_for_app_validation(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('type: mystery\nowner: alice\nvalue: hidden\n', encoding='utf-8')

            parsed = parse_key_file(path, root=root)

        self.assertEqual(parsed.type, 'mystery')

    def test_parse_rejects_non_mapping_yaml(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('- not\n- a mapping\n', encoding='utf-8')

            with self.assertRaises(YAMLError):
                parse_key_file(path, root=root)
