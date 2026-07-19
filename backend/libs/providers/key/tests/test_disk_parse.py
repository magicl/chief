# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from libs.providers.key.disk_parse import parse_key_outcome
from libs.providers.key.health_codes import INVALID_DECLARATION
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


class TestKeyDiskParseOutcome(OTestCase):
    """Verify two-stage parsing separates identity extraction from health outcomes."""

    def test_valid_static_declaration_parses_fully_with_ready_outcome(self) -> None:
        """A valid static declaration returns a full file with no health code."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work-openai.yaml'
            path.parent.mkdir()
            path.write_text('type: openai\nowner: alice\nvalue: sk-test\n', encoding='utf-8')

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, '')
        assert outcome.file is not None
        self.assertEqual(outcome.file.value, 'sk-test')
        self.assertEqual(outcome.name, 'work-openai')
        self.assertEqual(outcome.type, 'openai')
        self.assertEqual(outcome.owner, 'alice')
        self.assertEqual(outcome.source_path, 'keys/work-openai.yaml')
        self.assertTrue(outcome.source_rev.startswith('sha256:'))

    def test_parse_uses_explicit_name(self) -> None:
        """Prefer an explicit YAML name over the filename stem."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text(
                'name: personal-openai\ntype: openai\nowner: alice\nvalue: sk-test\n',
                encoding='utf-8',
            )

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.name, 'personal-openai')

    def test_valid_static_declaration_accepts_explicit_empty_value(self) -> None:
        """An explicitly empty static value still parses fully, distinct from missing."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'empty.yaml'
            path.parent.mkdir()
            path.write_text('type: openai\nowner: alice\nvalue:\n', encoding='utf-8')

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, '')
        assert outcome.file is not None
        self.assertEqual(outcome.file.value, '')

    def test_unknown_type_still_parses_for_app_validation(self) -> None:
        """Leave unknown types for callers; structural static parse succeeds."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('type: mystery\nowner: alice\nvalue: hidden\n', encoding='utf-8')

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, '')
        assert outcome.file is not None
        self.assertEqual(outcome.file.type, 'mystery')

    def test_valid_oauth_declaration_parses_fully_with_ready_outcome(self) -> None:
        """A valid OAuth declaration with scopes returns a full file with no health code."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work-google.yaml'
            path.parent.mkdir()
            path.write_text(
                'name: work-google\ntype: google\nowner: alice\nsource: oauth\nscopes:\n  - gmail_read\n',
                encoding='utf-8',
            )

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, '')
        assert outcome.file is not None
        self.assertEqual(outcome.file.auth_kind, 'oauth')
        self.assertIsNone(outcome.file.value)
        self.assertEqual(outcome.file.capabilities, ('gmail_read',))

    def test_missing_scopes_is_a_recoverable_invalid_declaration(self) -> None:
        """A resolvable owner/name with missing OAuth scopes still yields an identity."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work-google.yaml'
            path.parent.mkdir()
            path.write_text('name: work-google\ntype: google\nowner: alice\nsource: oauth\n', encoding='utf-8')

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, INVALID_DECLARATION)
        self.assertIsNone(outcome.file)
        self.assertEqual(outcome.name, 'work-google')
        self.assertEqual(outcome.type, 'google')
        self.assertEqual(outcome.owner, 'alice')

    def test_auth_kind_field_is_always_an_invalid_declaration(self) -> None:
        """``auth_kind`` is never disk syntax, regardless of otherwise-valid shapes."""
        documents = (
            'name: work\ntype: google\nowner: alice\nvalue: hidden\nauth_kind: static\n',
            'name: work\ntype: google\nowner: alice\nsource: oauth\nscopes: [gmail_read]\nauth_kind: oauth\n',
            'name: work\ntype: google\nowner: alice\nauth_kind: oauth\n',
        )
        for document in documents:
            with self.subTest(document=document), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'work.yaml'
                path.parent.mkdir()
                path.write_text(document, encoding='utf-8')

                outcome = parse_key_outcome(path, root=root)

            self.assertEqual(outcome.health_code, INVALID_DECLARATION)
            self.assertIsNone(outcome.file)
            self.assertEqual(outcome.name, 'work')
            self.assertEqual(outcome.owner, 'alice')

    def test_missing_type_is_a_recoverable_invalid_declaration(self) -> None:
        """A resolvable owner/name without a type still yields an identity."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work.yaml'
            path.parent.mkdir()
            path.write_text('name: work\nowner: alice\nvalue: hidden\n', encoding='utf-8')

            outcome = parse_key_outcome(path, root=root)

        self.assertEqual(outcome.health_code, INVALID_DECLARATION)
        self.assertIsNone(outcome.file)
        self.assertEqual(outcome.type, '')
        self.assertEqual(outcome.name, 'work')
        self.assertEqual(outcome.owner, 'alice')

    def test_mixed_and_form_specific_fields_are_invalid_declarations(self) -> None:
        """Reject fields that make static versus OAuth intent ambiguous as recoverable."""
        invalid_documents = (
            'name: work\ntype: google\nowner: alice\nvalue: hidden\nsource: oauth\nscopes: [gmail_read]\n',
            'name: work\ntype: google\nowner: alice\nsource: oauth\nscopes: [gmail_read]\nextra: hidden\n',
            'name: work\ntype: openai\nowner: alice\nvalue: hidden\nscopes: [gmail_read]\n',
            'name: work\ntype: openai\nowner: alice\nvalue: hidden\nsource: static\n',
        )
        for document in invalid_documents:
            with self.subTest(document=document), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'invalid.yaml'
                path.parent.mkdir()
                path.write_text(document, encoding='utf-8')

                outcome = parse_key_outcome(path, root=root)

            self.assertEqual(outcome.health_code, INVALID_DECLARATION)
            self.assertIsNone(outcome.file)
            self.assertEqual(outcome.name, 'work')

    def test_invalid_oauth_capability_lists_are_invalid_declarations(self) -> None:
        """Require at least one non-empty string capability identifier."""
        invalid_scopes = ('[]', 'gmail_read', '[gmail_read, ""]', '[gmail_read, 7]')
        for scopes in invalid_scopes:
            with self.subTest(scopes=scopes), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'invalid.yaml'
                path.parent.mkdir()
                path.write_text(
                    f'name: work\ntype: google\nowner: alice\nsource: oauth\nscopes: {scopes}\n',
                    encoding='utf-8',
                )

                outcome = parse_key_outcome(path, root=root)

            self.assertEqual(outcome.health_code, INVALID_DECLARATION)
            self.assertIsNone(outcome.file)

    def test_unresolvable_identity_still_raises(self) -> None:
        """Missing owner or name remains an unrecoverable, raised failure."""
        for document in ('type: openai\nvalue: hidden\n', 'type: openai\nowner: alice\nname: \nvalue: hidden\n'):
            with self.subTest(document=document), TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                path = root / 'keys' / 'work.yaml'
                path.parent.mkdir()
                path.write_text(document, encoding='utf-8')

                with self.assertRaises(ValueError):
                    parse_key_outcome(path, root=root)

    def test_non_mapping_and_unparseable_yaml_still_raises(self) -> None:
        """Unparseable or non-mapping YAML remains unrecoverable."""
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'file.yaml'
            path.parent.mkdir()
            path.write_text('- not\n- a mapping\n', encoding='utf-8')

            with self.assertRaises(YAMLError):
                parse_key_outcome(path, root=root)

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
                parse_key_outcome(path, root=root)

        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(sentinel, _retained_failure_text(caught.exception))

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
                    parse_key_outcome(path, root=root)

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
                parse_key_outcome(path, root=root)

        rendered = f'{caught.exception!s}\n{caught.exception!r}'
        self.assertNotIn('secret-sentinel', rendered)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('secret-sentinel', _disk_parse_traceback_locals(caught.exception))

    def test_invalid_declaration_never_retains_a_secret_value(self) -> None:
        """A recoverable outcome never carries a parsed file or raw secret text."""
        secret_sentinel = 'invalid-declaration-secret-sentinel'
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / 'keys' / 'work.yaml'
            path.parent.mkdir()
            path.write_text(
                f'name: work\ntype: openai\nowner: alice\nvalue: {secret_sentinel}\nauth_kind: static\n',
                encoding='utf-8',
            )

            outcome = parse_key_outcome(path, root=root)

        self.assertIsNone(outcome.file)
        self.assertNotIn(secret_sentinel, repr(outcome))
