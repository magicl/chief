# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from libs.providers.key.disk_parse import KeyDiskFile, parse_key_file
from yaml import YAMLError

from olib.py.django.test.cases import OTestCase


def _disk_parse_traceback_locals(exc: BaseException) -> str:
    """Render retained parser-frame locals for secret-sentinel assertions."""
    retained: list[dict[str, object]] = []
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get('__name__') == 'libs.providers.key.disk_parse':
            retained.append(dict(traceback.tb_frame.f_locals))
        traceback = traceback.tb_next
    return repr(retained)


def _retained_failure_text(value: object, *, seen: set[int] | None = None, depth: int = 0) -> str:
    """Render reachable failure state, including marks and parser-frame locals."""
    if seen is None:
        seen = set()
    if depth > 8 or id(value) in seen:
        return ''
    seen.add(id(value))
    rendered: list[str] = []
    for renderer in (str, repr):
        try:
            rendered.append(renderer(value))
        except Exception:  # pragma: no cover  # pylint: disable=broad-exception-caught
            rendered.append('<unrenderable>')
    if isinstance(value, BaseException):
        rendered.extend(_retained_failure_text(item, seen=seen, depth=depth + 1) for item in value.args)
        if value.__cause__ is not None:
            rendered.append(_retained_failure_text(value.__cause__, seen=seen, depth=depth + 1))
        if value.__context__ is not None:
            rendered.append(_retained_failure_text(value.__context__, seen=seen, depth=depth + 1))
        rendered.append(_retained_failure_text(vars(value), seen=seen, depth=depth + 1))
        traceback = value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_globals.get('__name__') == 'libs.providers.key.disk_parse':
                rendered.append(_retained_failure_text(traceback.tb_frame.f_locals, seen=seen, depth=depth + 1))
            traceback = traceback.tb_next
    elif isinstance(value, Mapping):
        for key, item in value.items():
            rendered.append(_retained_failure_text(key, seen=seen, depth=depth + 1))
            rendered.append(_retained_failure_text(item, seen=seen, depth=depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        rendered.extend(_retained_failure_text(item, seen=seen, depth=depth + 1) for item in value)
    elif hasattr(value, '__dict__'):
        rendered.append(_retained_failure_text(vars(value), seen=seen, depth=depth + 1))
    return '\n'.join(rendered)


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
                auth_kind='static',
                value='sk-test',
                capabilities=(),
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

    def test_malformed_flow_failure_retains_no_source_values(self) -> None:
        """Replace provider parser state with a fresh value-free YAML failure."""
        sentinel = 'malformed-flow-credential-secret-sentinel'
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'invalid.yaml'
            path.parent.mkdir()
            path.write_text(
                f'type: openai\nowner: alice\nvalue: [{sentinel}\n',
                encoding='utf-8',
            )

            with self.assertRaises(YAMLError) as caught:
                parse_key_file(path, root=root)

        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(sentinel, _retained_failure_text(caught.exception))

    def test_parse_oauth_declaration(self) -> None:
        """Return an OAuth declaration without treating capabilities as raw scopes."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work-google.yaml'
            path.parent.mkdir()
            path.write_text(
                'name: work-google\n'
                'type: google\n'
                'owner: user@example.com\n'
                'source: oauth\n'
                'scopes:\n'
                '  - drive_metadata\n'
                '  - gmail_read\n',
                encoding='utf-8',
            )

            parsed = parse_key_file(path, root=root)

        self.assertEqual(parsed.auth_kind, 'oauth')
        self.assertIsNone(parsed.value)
        self.assertEqual(parsed.capabilities, ('drive_metadata', 'gmail_read'))

    def test_parse_static_accepts_explicit_empty_value(self) -> None:
        """Distinguish an explicitly empty static value from a missing key."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'empty.yaml'
            path.parent.mkdir()
            path.write_text('type: openai\nowner: alice\nvalue:\n', encoding='utf-8')

            parsed = parse_key_file(path, root=root)

        self.assertEqual(parsed.auth_kind, 'static')
        self.assertEqual(parsed.value, '')
        self.assertEqual(parsed.capabilities, ())

    def test_parse_rejects_mixed_and_form_specific_fields(self) -> None:
        """Reject fields that could make static versus OAuth intent ambiguous."""
        invalid_documents = (
            'type: google\nowner: alice\nvalue: hidden\nsource: oauth\nscopes: [gmail_read]\n',
            'type: google\nowner: alice\nsource: oauth\nscopes: [gmail_read]\nextra: hidden\n',
            'type: openai\nowner: alice\nvalue: hidden\nscopes: [gmail_read]\n',
            'type: openai\nowner: alice\nvalue: hidden\nsource: static\n',
        )
        for document in invalid_documents:
            with self.subTest(document=document), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'invalid.yaml'
                path.parent.mkdir()
                path.write_text(document, encoding='utf-8')

                with self.assertRaises(ValueError):
                    parse_key_file(path, root=root)

    def test_parse_rejects_invalid_oauth_capability_lists(self) -> None:
        """Require at least one non-empty string capability identifier."""
        invalid_scopes = ('[]', 'gmail_read', '[gmail_read, ""]', '[gmail_read, 7]')
        for scopes in invalid_scopes:
            with self.subTest(scopes=scopes), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'invalid.yaml'
                path.parent.mkdir()
                path.write_text(
                    f'type: google\nowner: alice\nsource: oauth\nscopes: {scopes}\n',
                    encoding='utf-8',
                )

                with self.assertRaises(ValueError):
                    parse_key_file(path, root=root)

    def test_parse_rejects_duplicate_mapping_keys_without_values(self) -> None:
        """Reject every credential key duplicate with a value-free YAML failure."""
        duplicate_documents = {
            'value': (
                'type: openai\nowner: alice\n'
                'value: duplicate-value-first-secret-sentinel\n'
                'value: duplicate-value-second-secret-sentinel\n'
            ),
            'source': (
                'type: google\nowner: alice\nname: duplicate-source-secret-sentinel\n'
                'source: oauth\nsource: oauth\nscopes: [gmail_read]\n'
            ),
            'scopes': (
                'type: google\nowner: alice\nsource: oauth\n'
                'scopes: [duplicate-scopes-first-secret-sentinel]\n'
                'scopes: [duplicate-scopes-second-secret-sentinel]\n'
            ),
            'type': (
                'type: duplicate-type-first-secret-sentinel\n'
                'type: duplicate-type-second-secret-sentinel\n'
                'owner: alice\nvalue: hidden\n'
            ),
            'owner': (
                'type: openai\nowner: duplicate-owner-first-secret-sentinel\n'
                'owner: duplicate-owner-second-secret-sentinel\nvalue: hidden\n'
            ),
        }
        for field, document in duplicate_documents.items():
            with self.subTest(field=field), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'invalid.yaml'
                path.parent.mkdir()
                path.write_text(document, encoding='utf-8')

                with self.assertRaises(YAMLError) as caught:
                    parse_key_file(path, root=root)

                rendered = f'{caught.exception!s}\n{caught.exception!r}'
                self.assertNotIn('secret-sentinel', rendered)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn('secret-sentinel', _disk_parse_traceback_locals(caught.exception))

    def test_parse_rejects_merge_keys_without_values(self) -> None:
        """Reject YAML merge overrides before credential field construction."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'invalid.yaml'
            path.parent.mkdir()
            path.write_text(
                'defaults: &defaults\n'
                '  type: merge-type-secret-sentinel\n'
                '  value: merge-value-secret-sentinel\n'
                '<<: *defaults\n'
                'type: openai\n'
                'owner: alice\n'
                'value: explicit-value-secret-sentinel\n',
                encoding='utf-8',
            )

            with self.assertRaises(YAMLError) as caught:
                parse_key_file(path, root=root)

        rendered = f'{caught.exception!s}\n{caught.exception!r}'
        self.assertNotIn('secret-sentinel', rendered)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('secret-sentinel', _disk_parse_traceback_locals(caught.exception))
