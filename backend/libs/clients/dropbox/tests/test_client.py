# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the Django-free Dropbox metadata client foundation."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import dropbox
from libs.clients.dropbox.client import DropboxClient, _build_sdk, _sdk_path
from libs.clients.dropbox.config import (
    DropboxConfig,
    DropboxRoot,
    _ascii_lower,
    is_path_within,
    normalize_dropbox_path,
    parse_dropbox_config,
)
from libs.clients.dropbox.errors import (
    DropboxAPIError,
    DropboxAuthError,
    DropboxConfigError,
    DropboxForbiddenError,
    DropboxInvalidCursorError,
    DropboxNotFoundError,
    DropboxOutsideRootError,
    DropboxRateLimitedError,
)

from olib.py.django.test.cases import OTestCase

# Foundation behavior intentionally exercises private seams before Task 7 adds operations.
# pylint: disable=protected-access


def _valid_config(**overrides: object) -> dict[str, object]:
    """Return valid non-secret Dropbox configuration with optional replacements."""
    config: dict[str, object] = {'roots': [{'id': 'projects', 'path': '/Projects'}]}
    config.update(overrides)
    return config


def _credential(**overrides: object) -> str:
    """Return one valid refresh credential JSON value."""
    payload: dict[str, object] = {
        'app_key': 'app-key',
        'app_secret': 'app-secret',
        'refresh_token': 'refresh-token',
    }
    payload.update(overrides)
    return json.dumps(payload)


def _client_traceback_locals(exc: BaseException) -> list[tuple[str, dict[str, object]]]:
    """Collect locals retained by Dropbox client frames in a failure traceback."""
    retained: list[tuple[str, dict[str, object]]] = []
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith('/libs/clients/dropbox/client.py'):
            retained.append((traceback.tb_frame.f_code.co_name, dict(traceback.tb_frame.f_locals)))
        traceback = traceback.tb_next
    return retained


class TestDropboxConfig(OTestCase):
    """Validate immutable Dropbox namespace and root addressing."""

    def test_requires_nonempty_roots_list(self) -> None:
        """Reject omitted, empty, and non-list roots."""
        configs: tuple[dict[str, Any], ...] = (
            {},
            {'roots': []},
            {'roots': ()},
            {'roots': 'root'},
        )
        for config in configs:
            with self.subTest(config=config), self.assertRaises(DropboxConfigError):
                parse_dropbox_config(config)

    def test_rejects_malformed_roots_and_duplicate_aliases(self) -> None:
        """Reject malformed records and aliases that collide after trimming."""
        malformed: tuple[dict[str, Any], ...] = (
            {'roots': [None]},
            {'roots': [{}]},
            {'roots': [{'id': '', 'path': '/Projects'}]},
            {'roots': [{'id': 1, 'path': '/Projects'}]},
            {'roots': [{'id': 'projects', 'path': ''}]},
            {'roots': [{'id': 'projects', 'path': 1}]},
            {
                'roots': [
                    {'id': 'projects', 'path': '/Projects'},
                    {'id': ' projects ', 'path': '/Other'},
                ]
            },
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(DropboxConfigError):
                parse_dropbox_config(config)

    def test_rejects_duplicate_lowercase_paths(self) -> None:
        """Reject roots that address the same Dropbox path with ASCII casing differences."""
        with self.assertRaises(DropboxConfigError):
            parse_dropbox_config(
                {
                    'roots': [
                        {'id': 'one', 'path': '/Projects'},
                        {'id': 'two', 'path': '/projects'},
                    ]
                }
            )

    def test_does_not_merge_paths_that_only_unicode_casefold_equates(self) -> None:
        """Keep sharp-s and double-s roots distinct like authoritative path_lower values."""
        parsed = parse_dropbox_config(
            {
                'roots': [
                    {'id': 'sharp-s', 'path': '/Straße'},
                    {'id': 'double-s', 'path': '/STRASSE'},
                ]
            }
        )
        self.assertEqual([root.path for root in parsed.roots], ['/Straße', '/STRASSE'])

    def test_does_not_merge_paths_across_unicode_lowercase_generations(self) -> None:
        """Keep legacy provider-distinct Unicode locators separate during config parsing."""
        parsed = parse_dropbox_config(
            {
                'roots': [
                    {'id': 'upper', 'path': '/Ꙋ'},
                    {'id': 'lower', 'path': '/ꙋ'},
                ]
            }
        )
        self.assertEqual([root.path for root in parsed.roots], ['/Ꙋ', '/ꙋ'])

    def test_parses_optional_namespace_and_returns_frozen_slots_records(self) -> None:
        """Normalize non-secret values into immutable compact records."""
        parsed = parse_dropbox_config(
            {
                'namespace_id': ' team-space ',
                'roots': [{'id': ' projects ', 'path': '/Projects'}],
            }
        )
        self.assertEqual(
            parsed,
            DropboxConfig(
                namespace_id='team-space',
                roots=(DropboxRoot(id='projects', path='/Projects'),),
            ),
        )
        self.assertFalse(hasattr(parsed, '__dict__'))
        with self.assertRaises(FrozenInstanceError):
            parsed.namespace_id = 'other'  # type: ignore[misc]

    def test_rejects_blank_namespace_and_unknown_fields(self) -> None:
        """Reject supplied empty namespaces and unsupported config keys."""
        malformed = (
            _valid_config(namespace_id=''),
            _valid_config(namespace_id='  '),
            _valid_config(namespace_id=1),
            _valid_config(extra=True),
            {'roots': [{'id': 'projects', 'path': '/Projects', 'extra': True}]},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(DropboxConfigError):
                parse_dropbox_config(config)

    def test_path_normalization_accepts_only_canonical_absolute_paths(self) -> None:
        """Preserve canonical paths and reject ambiguous or relative forms."""
        self.assertEqual(normalize_dropbox_path('/'), '/')
        self.assertEqual(normalize_dropbox_path('/Projects/Q3'), '/Projects/Q3')
        invalid: tuple[object, ...] = (
            None,
            '',
            'Projects',
            '.',
            '..',
            '/.',
            '/..',
            '/Projects/./Q3',
            '/Projects/../Q3',
            '//Projects',
            '/Projects//Q3',
            '/Projects/',
        )
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(DropboxConfigError):
                normalize_dropbox_path(path)  # type: ignore[arg-type]

    def test_path_containment_uses_authoritative_path_lower_segments(self) -> None:
        """Compare provider-normalized lowercase paths only at segment boundaries."""
        self.assertTrue(is_path_within('/projects', '/projects'))
        self.assertTrue(is_path_within('/projects', '/projects/q3'))
        self.assertTrue(is_path_within('/', '/projects/q3'))
        self.assertFalse(is_path_within('/projects', '/projects2'))
        self.assertFalse(is_path_within('/straße', '/strasse/q3'))

    def test_path_containment_does_not_renormalize_authoritative_unicode(self) -> None:
        """Keep Dropbox legacy path_lower values distinct from modern Python lowercase."""
        self.assertFalse(is_path_within('/ꙋ', '/Ꙋ/file'))

    def test_invalid_config_does_not_resolve_or_build_credentials(self) -> None:
        """Fail structural validation before touching either secret boundary."""
        supplier = MagicMock()
        factory = MagicMock()
        with self.assertRaises(DropboxConfigError):
            DropboxClient(
                token_supplier=supplier,
                config={'roots': []},
                instance_id='dropbox',
                sdk_factory=factory,
            )
        supplier.assert_not_called()
        factory.assert_not_called()


class TestDropboxAuth(OTestCase):
    """Verify refresh credentials and operation-local namespace selection."""

    @patch('dropbox.Dropbox')
    def test_default_factory_disables_sdk_retries(self, sdk_constructor: MagicMock) -> None:
        """Give the wrapper exclusive ownership of bounded provider retries."""
        _build_sdk(_credential())
        sdk_constructor.assert_called_once_with(
            oauth2_refresh_token='refresh-token',
            app_key='app-key',
            app_secret='app-secret',
            max_retries_on_error=0,
            max_retries_on_rate_limit=0,
        )

    def test_credential_requires_json_object_and_three_nonempty_strings(self) -> None:
        """Reject malformed JSON and missing, blank, or non-string refresh fields."""
        malformed = (
            'not-json-private',
            '[]',
            '{}',
            _credential(app_key=''),
            _credential(app_secret='  '),
            _credential(refresh_token=1),
        )
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(DropboxAuthError) as caught:
                _build_sdk(raw)
            self.assertNotIn(raw, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_namespace_is_selected_before_the_files_api_call(self) -> None:
        """Derive an operation-local namespace SDK before resolving root metadata."""
        namespaced_sdk = MagicMock()
        base_sdk = MagicMock()
        base_sdk.with_path_root.return_value = namespaced_sdk
        namespaced_sdk.files_get_metadata.return_value = dropbox.files.FolderMetadata(
            name='Projects',
            id='id:projects',
            path_lower='/projects',
            path_display='/Projects',
        )
        marker = object()
        with patch('dropbox.common.PathRoot.namespace_id', return_value=marker) as namespace:
            result = DropboxClient(
                token_supplier=_credential,
                config=_valid_config(namespace_id='team-space'),
                instance_id='dropbox',
                sdk_factory=lambda _raw: base_sdk,
            ).list_roots()
        namespace.assert_called_once_with('team-space')
        base_sdk.with_path_root.assert_called_once_with(marker)
        base_sdk.files_get_metadata.assert_not_called()
        namespaced_sdk.files_get_metadata.assert_called_once_with('/Projects')
        self.assertEqual(result['items'][0]['id'], 'id:projects')

    def test_list_roots_builds_a_fresh_sdk_per_public_operation(self) -> None:
        """Resolve credentials and build a new SDK for each root-list invocation."""
        builds: list[MagicMock] = []

        def factory(_raw: str) -> MagicMock:
            """Create and record one operation-local fake SDK."""
            sdk = MagicMock()
            sdk.files_get_metadata.return_value = dropbox.files.FolderMetadata(
                name='Projects',
                id='id:projects',
                path_lower='/projects',
                path_display='/Projects',
            )
            builds.append(sdk)
            return sdk

        supplier = MagicMock(return_value=_credential())
        client = DropboxClient(
            token_supplier=supplier,
            config=_valid_config(),
            instance_id='dropbox',
            sdk_factory=factory,
        )
        client.list_roots()
        client.list_roots()
        self.assertEqual(supplier.call_count, 2)
        self.assertEqual(len(builds), 2)
        self.assertIsNot(builds[0], builds[1])

    def test_namespaced_operation_closes_effective_sdk_once(self) -> None:
        """Close the clone whose shared session belongs to the operation."""
        namespaced_sdk = MagicMock()
        base_sdk = MagicMock()
        base_sdk.with_path_root.return_value = namespaced_sdk
        namespaced_sdk.files_get_metadata.return_value = dropbox.files.FolderMetadata(
            name='Projects',
            id='id:projects',
            path_lower='/projects',
            path_display='/Projects',
        )
        DropboxClient(
            token_supplier=_credential,
            config=_valid_config(namespace_id='team-space'),
            instance_id='dropbox',
            sdk_factory=lambda _raw: base_sdk,
        ).list_roots()
        namespaced_sdk.close.assert_called_once_with()
        base_sdk.close.assert_not_called()

    def test_operation_failure_is_preserved_when_close_fails(self) -> None:
        """Never replace a typed operation failure with best-effort cleanup failure."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = dropbox.exceptions.HttpError(
            'PRIVATE-REQUEST',
            403,
            'PRIVATE-BODY',
        )
        sdk.close.side_effect = RuntimeError('PRIVATE-CLOSE-BODY')
        client = DropboxClient(
            token_supplier=_credential,
            config=_valid_config(),
            instance_id='dropbox',
            sdk_factory=lambda _raw: sdk,
        )
        with self.assertRaises(DropboxForbiddenError) as caught:
            client.list_roots()
        sdk.close.assert_called_once_with()
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_namespace_clone_failure_closes_base_sdk(self) -> None:
        """Close the original session when namespace clone construction cannot finish."""
        base_sdk = MagicMock()
        base_sdk.with_path_root.side_effect = RuntimeError('PRIVATE-CLONE-BODY')
        client = DropboxClient(
            token_supplier=_credential,
            config=_valid_config(namespace_id='team-space'),
            instance_id='dropbox',
            sdk_factory=lambda _raw: base_sdk,
        )
        with self.assertRaises(DropboxAuthError) as caught:
            client.list_roots()
        base_sdk.close.assert_called_once_with()
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_constructor_retains_no_plaintext_or_sdk(self) -> None:
        """Retain only lazy suppliers, factories, and validated non-secret config."""
        supplier = MagicMock(return_value=_credential())
        factory = MagicMock()
        client = DropboxClient(
            token_supplier=supplier,
            config=_valid_config(),
            instance_id='dropbox',
            sdk_factory=factory,
        )
        supplier.assert_not_called()
        factory.assert_not_called()
        retained_names = {name.removeprefix('_') for name in vars(client)}
        self.assertNotIn('sdk', retained_names)
        self.assertNotIn('credential', repr(vars(client)))

    def test_malformed_credential_traceback_releases_secret_values(self) -> None:
        """Clear raw and parsed credential data from every retained production frame."""
        app_secret = 'APP-SECRET-UNIQUE-SENTINEL'
        refresh_token = 'REFRESH-TOKEN-UNIQUE-SENTINEL'
        raw = _credential(app_secret=app_secret, refresh_token=refresh_token)
        client = DropboxClient(
            token_supplier=lambda: raw,
            config=_valid_config(),
            instance_id='dropbox',
            sdk_factory=_build_sdk,
        )
        with patch('dropbox.Dropbox', side_effect=RuntimeError('PRIVATE-PROVIDER-BODY')):
            try:
                client.list_roots()
            except DropboxAuthError as failure:
                retained = repr(_client_traceback_locals(failure))
                self.assertNotIn(raw, retained)
                self.assertNotIn(app_secret, retained)
                self.assertNotIn(refresh_token, retained)
                self.assertNotIn('PRIVATE-PROVIDER-BODY', retained)
                self.assertIsNone(failure.__cause__)
                self.assertIsNone(failure.__context__)
            else:
                self.fail('credential build did not raise DropboxAuthError')


class TestDropboxNormalization(OTestCase):
    """Verify Dropbox metadata maps to the common metadata-only shape."""

    def _client(self) -> DropboxClient:
        """Build a client for direct foundation seam testing."""
        return DropboxClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='dropbox',
        )

    def test_normalizes_folder_metadata_and_parent_path(self) -> None:
        """Map folders while preserving display path and normalized parent reference."""
        raw = dropbox.files.FolderMetadata(
            name='Q3',
            id='id:q3',
            path_lower='/projects/q3',
            path_display='/Projects/Q3',
        )
        self.assertEqual(
            self._client()._normalize_item(raw, root_alias='projects'),
            {
                'provider': 'dropbox',
                'root': 'projects',
                'id': 'id:q3',
                'name': 'Q3',
                'kind': 'folder',
                'mime_type': None,
                'size': None,
                'modified_at': None,
                'parent_refs': ['/Projects'],
                'path': '/Projects/Q3',
                'web_url': None,
                'provider_metadata': {},
            },
        )

    def test_normalizes_file_size_timestamp_and_revision_only(self) -> None:
        """Expose file metadata without content, links, hashes, or preview fields."""
        modified = datetime(2026, 7, 18, 12, 30, tzinfo=UTC)
        raw = dropbox.files.FileMetadata(
            name='report.txt',
            id='id:report',
            client_modified=modified,
            server_modified=modified,
            rev='deadbeef1',
            size=42,
            path_lower='/projects/report.txt',
            path_display='/Projects/report.txt',
            content_hash='abcdef01' * 8,
            preview_url='PRIVATE-PREVIEW-URL',
        )
        item = self._client()._normalize_item(raw, root_alias='projects')
        self.assertEqual(item['kind'], 'file')
        self.assertEqual(item['size'], 42)
        self.assertEqual(item['modified_at'], '2026-07-18T12:30:00+00:00')
        self.assertEqual(item['provider_metadata'], {'rev': 'deadbeef1'})
        self.assertEqual(item['parent_refs'], ['/Projects'])
        self.assertIsNone(item['web_url'])
        self.assertNotIn('abcdef01' * 8, repr(item))
        self.assertNotIn('PRIVATE-PREVIEW-URL', repr(item))

    def test_root_path_has_no_parent_reference(self) -> None:
        """Represent the Dropbox namespace root without an artificial parent."""
        raw = dropbox.files.FolderMetadata(
            name='',
            id='id:root',
            path_lower='/',
            path_display='/',
        )
        item = self._client()._normalize_item(raw, root_alias='all')
        self.assertEqual(item['parent_refs'], [])
        self.assertEqual(item['path'], '/')

    def test_sdk_root_path_uses_empty_string(self) -> None:
        """Translate configured slash to the SDK's documented root locator."""
        self.assertEqual(_sdk_path('/'), '')
        self.assertEqual(_sdk_path('/Projects'), '/Projects')

    def test_list_roots_synthesizes_namespace_root_with_operation_sdk(self) -> None:
        """Build and close an SDK without calling unsupported root metadata."""
        supplier = MagicMock(return_value=_credential())
        factory = MagicMock()
        result = DropboxClient(
            token_supplier=supplier,
            config={'roots': [{'id': 'all', 'path': '/'}]},
            instance_id='dropbox',
            sdk_factory=factory,
        ).list_roots()
        self.assertEqual(
            result,
            {
                'items': [
                    {
                        'provider': 'dropbox',
                        'root': 'all',
                        'id': '/',
                        'name': '/',
                        'kind': 'folder',
                        'mime_type': None,
                        'size': None,
                        'modified_at': None,
                        'parent_refs': [],
                        'path': '/',
                        'web_url': None,
                        'provider_metadata': {},
                    }
                ],
                'next_cursor': None,
            },
        )
        supplier.assert_called_once_with()
        factory.assert_called_once_with(_credential())
        factory.return_value.files_get_metadata.assert_not_called()
        factory.return_value.close.assert_called_once_with()

    def test_mixed_roots_skip_unsupported_root_metadata_call(self) -> None:
        """Use one SDK only for roots whose metadata endpoint accepts the path."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = dropbox.files.FolderMetadata(
            name='Projects',
            id='id:projects',
            path_lower='/projects',
            path_display='/Projects',
        )
        result = DropboxClient(
            token_supplier=_credential,
            config={
                'roots': [
                    {'id': 'all', 'path': '/'},
                    {'id': 'projects', 'path': '/Projects'},
                ]
            },
            instance_id='dropbox',
            sdk_factory=lambda _raw: sdk,
        ).list_roots()
        sdk.files_get_metadata.assert_called_once_with('/Projects')
        sdk.close.assert_called_once_with()
        self.assertEqual([item['id'] for item in result['items']], ['/', 'id:projects'])

    def test_malformed_metadata_traceback_releases_provider_values(self) -> None:
        """Clear malformed response values before a normalization failure propagates."""
        raw = dropbox.files.FileMetadata(
            name='report.txt',
            id='id:report',
            client_modified=datetime(2026, 7, 18, tzinfo=UTC),
            server_modified=datetime(2026, 7, 18, tzinfo=UTC),
            rev='deadbeef1',
            size=42,
            path_lower='/projects/report.txt',
            path_display='/Projects/report.txt',
        )
        raw._path_display_value = 'PRIVATE-PROVIDER-RESPONSE'  # pylint: disable=protected-access
        try:
            self._client()._normalize_item(raw, root_alias='projects')
        except DropboxAPIError as failure:
            frames = _client_traceback_locals(failure)
            self.assertNotIn('PRIVATE-PROVIDER-RESPONSE', repr(frames))
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is raw for value in retained_values))
        else:
            self.fail('malformed metadata did not raise DropboxAPIError')


class TestDropboxExecution(OTestCase):
    """Verify bounded retries and safe typed provider failures."""

    def _client(self, *, sleeps: list[float] | None = None) -> DropboxClient:
        """Build a client with an observable sleep boundary."""
        observed = sleeps if sleeps is not None else []
        return DropboxClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='dropbox',
            sleep_fn=observed.append,
        )

    def test_retries_rate_limit_once_with_finite_capped_backoff(self) -> None:
        """Retry one rate response and cap its provider-directed delay."""
        for backoff, expected in ((2.5, 2.5), (float('inf'), 0.0), (3600, 60.0)):
            operation = MagicMock(
                side_effect=[
                    dropbox.exceptions.RateLimitError('request-private', backoff=backoff),
                    {'ok': True},
                ]
            )
            sleeps: list[float] = []
            with self.subTest(backoff=backoff):
                self.assertEqual(
                    self._client(sleeps=sleeps)._execute(operation, operation_name='list roots'), {'ok': True}
                )
                self.assertEqual(operation.call_count, 2)
                self.assertEqual(sleeps, [expected])

    def test_retries_transient_transport_failure_once(self) -> None:
        """Retry a transient transport failure without an unbounded delay."""
        operation = MagicMock(side_effect=[TimeoutError('PRIVATE-BODY'), {'ok': True}])
        sleeps: list[float] = []
        result = self._client(sleeps=sleeps)._execute(operation, operation_name='list roots')
        self.assertEqual(result, {'ok': True})
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(sleeps, [0.0])

    def test_exhausted_rate_limit_maps_without_provider_body(self) -> None:
        """Raise the typed rate failure after the one allowed retry."""
        operation = MagicMock(
            side_effect=[
                dropbox.exceptions.RateLimitError('PRIVATE-REQUEST', backoff=1),
                dropbox.exceptions.RateLimitError('PRIVATE-REQUEST', backoff=1),
            ]
        )
        with self.assertRaises(DropboxRateLimitedError) as caught:
            self._client(sleeps=[])._execute(operation, operation_name='search metadata')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_provider_statuses_map_to_safe_typed_failures(self) -> None:
        """Map auth, forbidden, missing, and remaining statuses without bodies."""
        cases = (
            (dropbox.exceptions.AuthError('PRIVATE', object()), DropboxAuthError),
            (dropbox.exceptions.HttpError('PRIVATE', 403, 'PRIVATE-BODY'), DropboxForbiddenError),
            (dropbox.exceptions.HttpError('PRIVATE', 404, 'PRIVATE-BODY'), DropboxNotFoundError),
            (dropbox.exceptions.HttpError('PRIVATE', 418, 'PRIVATE-BODY'), DropboxAPIError),
        )
        for provider_failure, expected in cases:
            operation = MagicMock(side_effect=provider_failure)
            with self.subTest(expected=expected), self.assertRaises(expected) as caught:
                self._client()._execute(operation, operation_name='get metadata')
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_namespace_no_permission_maps_to_forbidden(self) -> None:
        """Recognize the official non-ApiError namespace permission shape safely."""
        provider_failure = dropbox.exceptions.PathRootError(
            'PRIVATE-REQUEST',
            dropbox.common.PathRootError.no_permission,
        )
        operation = MagicMock(side_effect=provider_failure)
        with self.assertRaises(DropboxForbiddenError) as caught:
            self._client()._execute(operation, operation_name='resolve root')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('PRIVATE-REQUEST', repr(_client_traceback_locals(caught.exception)))

    def test_failure_traceback_releases_operation_and_provider_objects(self) -> None:
        """Remove operation closures, SDKs, metadata, and provider failures from tracebacks."""
        sdk = MagicMock()
        sdk.credential_marker = 'PRIVATE-CREDENTIAL-OBJECT'
        sdk.files_get_metadata.side_effect = dropbox.exceptions.HttpError(
            'PRIVATE-REQUEST',
            403,
            'PRIVATE-BODY',
        )
        client = DropboxClient(
            token_supplier=_credential,
            config=_valid_config(),
            instance_id='dropbox',
            sdk_factory=lambda _raw: sdk,
        )
        try:
            client.list_roots()
        except DropboxForbiddenError as failure:
            frames = _client_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is sdk for value in retained_values))
            self.assertNotIn('PRIVATE-BODY', repr(frames))
            for _name, values in frames:
                for local_name in ('sdk', 'metadata', 'provider_failure', 'call'):
                    if local_name in values:
                        self.assertIsNone(values[local_name])
        else:
            self.fail('provider failure did not raise DropboxForbiddenError')


def _folder(name: str, item_id: str, path: str, *, path_lower: str | None = None) -> Any:
    """Build provider folder metadata with an optionally authoritative lower path."""
    return dropbox.files.FolderMetadata(
        name=name,
        id=item_id,
        path_lower=path_lower or _ascii_lower(path),
        path_display=path,
    )


def _file(name: str, item_id: str, path: str, *, path_lower: str | None = None) -> Any:
    """Build provider file metadata for operation tests."""
    modified = datetime(2026, 7, 18, tzinfo=UTC)
    return dropbox.files.FileMetadata(
        name=name,
        id=item_id,
        client_modified=modified,
        server_modified=modified,
        rev='deadbeef1',
        size=1,
        path_lower=path_lower or _ascii_lower(path),
        path_display=path,
    )


class TestDropboxOperations(OTestCase):
    """Require root-safe Dropbox list, lookup, search, and cursor behavior."""

    def _client(self, sdk: MagicMock, *, config: dict[str, Any] | None = None, instance: str = 'dbx') -> DropboxClient:
        """Build one operation client around an observable SDK."""
        return DropboxClient(
            token_supplier=_credential,
            config=config or _valid_config(),
            instance_id=instance,
            sdk_factory=lambda _raw: sdk,
        )

    def test_list_roots_uses_authoritative_paths_in_config_order(self) -> None:
        """Resolve configured locators and retain provider-authoritative current metadata."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _folder('Zulu', 'id:z', '/Zulu', path_lower='/zulu'),
            _folder('Alpha', 'id:a', '/Elsewhere', path_lower='/elsewhere'),
        ]
        client = self._client(
            sdk,
            config={
                'roots': [
                    {'id': 'z', 'path': '/Zulu'},
                    {'id': 'a', 'path': '/Alpha'},
                ]
            },
        )
        result = client.list_roots()
        self.assertEqual([item['id'] for item in result['items']], ['id:z', 'id:a'])
        self.assertEqual(
            sdk.files_get_metadata.call_args_list,
            [((('/Zulu',)), {}), ((('/Alpha',)), {})],
        )
        sdk.close.assert_called_once_with()

    def test_root_resolution_trusts_provider_path_lower_not_display_case_conversion(self) -> None:
        """Use metadata returned for the configured locator without folding display paths."""
        sdk = MagicMock()
        root = _folder('Legacy', 'id:root', '/Provider Display', path_lower='/Ꙋ')
        child = _file('item', 'id:item', '/Provider Display/item', path_lower='/Ꙋ/item')
        sdk.files_get_metadata.side_effect = [root, child]
        result = self._client(
            sdk,
            config={'roots': [{'id': 'legacy', 'path': '/ꙋ'}]},
        ).get_metadata(root='legacy', item_ref='id:item')
        self.assertEqual(result['item']['id'], 'id:item')
        self.assertEqual(sdk.files_get_metadata.call_args_list[0].args, ('/ꙋ',))

    def test_root_slash_uses_empty_sdk_path_for_list_and_search(self) -> None:
        """Address namespace root with the SDK empty path while authorizing slash."""
        sdk = MagicMock()
        sdk.files_list_folder.return_value = SimpleNamespace(entries=[], has_more=False, cursor='unused')
        sdk.files_search_v2.return_value = SimpleNamespace(matches=[], has_more=False, cursor='unused')
        client = self._client(sdk, config={'roots': [{'id': 'all', 'path': '/'}]})
        client.list_folder(root='all', max_results=7)
        sdk.files_list_folder.assert_called_once_with('', recursive=False, limit=7)
        client.search(root='all', query='report', max_results=6)
        args = sdk.files_search_v2.call_args
        self.assertEqual(args.args, ('report',))
        self.assertEqual(args.kwargs['options'].path, '')
        self.assertEqual(args.kwargs['options'].max_results, 6)

    def test_public_input_bounds_fail_before_sdk_construction(self) -> None:
        """Reject oversized aliases, refs, queries, and kind lists before credentials."""
        factory = MagicMock()
        client = DropboxClient(
            token_supplier=_credential,
            config=_valid_config(),
            instance_id='dbx',
            sdk_factory=factory,
        )
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.list_folder(root='x' * 257),
            lambda: client.list_folder(root='projects', folder_ref='x' * 4_097),
            lambda: client.get_metadata(root='projects', item_ref='x' * 4_097),
            lambda: client.search(root='projects', query='x' * 1_001),
            lambda: client.search(root='projects', query='x', kinds=('file', 'folder', 'file')),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(DropboxConfigError):
                operation()
        factory.assert_not_called()

    def test_list_folder_resolves_explicit_ref_and_rechecks_every_result(self) -> None:
        """Authorize selected metadata and fail closed when any child leaves the root."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _folder('Projects', 'id:root', '/Projects'),
            _folder('Q3', 'id:q3', '/Projects/Q3'),
        ]
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[
                _file('ok.txt', 'id:ok', '/Projects/Q3/ok.txt'),
                _file('bad.txt', 'id:bad', '/Projects2/bad.txt'),
            ],
            has_more=False,
            cursor='unused',
        )
        with self.assertRaises(DropboxOutsideRootError):
            self._client(sdk).list_folder(root='projects', folder_ref='id:q3', max_results=8)
        sdk.files_list_folder.assert_called_once_with('/Projects/Q3', recursive=False, limit=8)
        sdk.close.assert_called_once_with()

    def test_list_folder_buffers_initial_overrun_without_skipping_entries(self) -> None:
        """Cap an oversized initial page and resume every entry in provider order."""
        sdk = MagicMock()
        root = _folder('Projects', 'id:root', '/Projects')
        first = _file('a.txt', 'id:a', '/Projects/a.txt')
        second = _file('b.txt', 'id:b', '/Projects/b.txt')
        third = _file('c.txt', 'id:c', '/Projects/c.txt')
        sdk.files_get_metadata.side_effect = [root, root, third]
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[first, second, third],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)

        page_one = client.list_folder(root='projects', max_results=2)
        page_two = client.list_folder(root='projects', cursor=page_one['next_cursor'], max_results=2)

        self.assertEqual([item['id'] for item in page_one['items']], ['id:a', 'id:b'])
        self.assertEqual([item['id'] for item in page_two['items']], ['id:c'])
        self.assertIsNone(page_two['next_cursor'])
        sdk.files_list_folder.assert_called_once_with('/Projects', recursive=False, limit=2)
        sdk.files_list_folder_continue.assert_not_called()

    def test_list_folder_buffers_continuation_overrun_without_skipping_entries(self) -> None:
        """Cap an oversized continuation and return each entry once in provider order."""
        sdk = MagicMock()
        root = _folder('Projects', 'id:root', '/Projects')
        first = _file('a.txt', 'id:a', '/Projects/a.txt')
        second = _file('b.txt', 'id:b', '/Projects/b.txt')
        third = _file('c.txt', 'id:c', '/Projects/c.txt')
        fourth = _file('d.txt', 'id:d', '/Projects/d.txt')
        sdk.files_get_metadata.side_effect = [root, root, root, fourth]
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[first],
            has_more=True,
            cursor='provider-next',
        )
        sdk.files_list_folder_continue.return_value = SimpleNamespace(
            entries=[second, third, fourth],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)

        page_one = client.list_folder(root='projects', max_results=2)
        page_two = client.list_folder(root='projects', cursor=page_one['next_cursor'], max_results=2)
        page_three = client.list_folder(root='projects', cursor=page_two['next_cursor'], max_results=2)

        returned = page_one['items'] + page_two['items'] + page_three['items']
        self.assertEqual([item['id'] for item in returned], ['id:a', 'id:b', 'id:c', 'id:d'])
        self.assertTrue(all(len(page['items']) <= 2 for page in (page_one, page_two, page_three)))
        self.assertIsNone(page_three['next_cursor'])
        sdk.files_list_folder_continue.assert_called_once_with('provider-next')

    def test_list_folder_reauthorizes_buffered_entry_on_resume(self) -> None:
        """Treat buffered IDs as hints and reject an entry moved outside before resume."""
        sdk = MagicMock()
        root = _folder('Projects', 'id:root', '/Projects')
        sdk.files_get_metadata.side_effect = [
            root,
            root,
            _file('b.txt', 'id:b', '/Projects2/b.txt'),
        ]
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[
                _file('a.txt', 'id:a', '/Projects/a.txt'),
                _file('b.txt', 'id:b', '/Projects/b.txt'),
            ],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)
        first = client.list_folder(root='projects', max_results=1)

        with self.assertRaises(DropboxOutsideRootError):
            client.list_folder(root='projects', cursor=first['next_cursor'], max_results=1)

    def test_list_folder_reauthorizes_buffered_entry_as_direct_child(self) -> None:
        """Reject a forged or moved buffered ref under a sibling authorized folder."""
        sdk = MagicMock()
        root = _folder('Projects', 'id:root', '/Projects')
        sdk.files_get_metadata.side_effect = [
            root,
            root,
            _file('b.txt', 'id:b', '/Projects/Other/b.txt'),
        ]
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[
                _file('a.txt', 'id:a', '/Projects/a.txt'),
                _file('b.txt', 'id:b', '/Projects/b.txt'),
            ],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)
        first = client.list_folder(root='projects', max_results=1)

        with self.assertRaises(DropboxOutsideRootError):
            client.list_folder(root='projects', cursor=first['next_cursor'], max_results=1)

    def test_list_folder_rejects_more_page_without_provider_cursor(self) -> None:
        """Fail closed instead of skipping entries when Dropbox omits its continuation."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[_file('a.txt', 'id:a', '/Projects/a.txt')],
            has_more=True,
            cursor=None,
        )
        with self.assertRaises(DropboxAPIError):
            self._client(sdk).list_folder(root='projects', max_results=1)

    def test_get_metadata_accepts_current_root_and_rejects_outside_id(self) -> None:
        """Resolve references from current provider metadata before path authorization."""
        root = _folder('Projects', 'id:root', '/Projects')
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [root, root]
        result = self._client(sdk).get_metadata(root='projects', item_ref='id:root')
        self.assertEqual(result['item']['id'], 'id:root')
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [root, _file('bad', 'id:bad', '/Projects2/bad')]
        with self.assertRaises(DropboxOutsideRootError):
            self._client(sdk).get_metadata(root='projects', item_ref='id:bad')

    def test_file_root_supports_metadata_but_rejects_list_and_search(self) -> None:
        """Permit configured file inspection while denying folder-only operations."""
        config = {'roots': [{'id': 'single', 'path': '/Single.txt'}]}
        for operation in ('list', 'search'):
            sdk = MagicMock()
            sdk.files_get_metadata.return_value = _file('Single.txt', 'id:single', '/Single.txt')
            client = self._client(sdk, config=config)
            if operation == 'list':
                with self.assertRaises(DropboxConfigError):
                    client.list_folder(root='single')
            else:
                with self.assertRaises(DropboxConfigError):
                    client.search(root='single', query='single')
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _file('Single.txt', 'id:single', '/Single.txt'),
            _file('Single.txt', 'id:single', '/Single.txt'),
        ]
        self.assertEqual(
            self._client(sdk, config=config).get_metadata(root='single', item_ref='id:single')['item']['id'],
            'id:single',
        )

    def test_search_uses_native_calls_filters_kinds_and_continues_five_pages(self) -> None:
        """Preserve native ranking while filtering kinds and bounding provider pages."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        folder = _folder('Reports', 'id:folder', '/Projects/Reports')
        file = _file('report.txt', 'id:file', '/Projects/report.txt')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: folder))],
            has_more=True,
            cursor='c1',
        )
        sdk.files_search_continue_v2.side_effect = [
            SimpleNamespace(
                matches=[SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: file))],
                has_more=True,
                cursor=f'c{index}',
            )
            for index in range(2, 6)
        ]
        result = self._client(sdk).search(root='projects', query='report', kinds=('file',), max_results=10)
        self.assertEqual([item['id'] for item in result['items']], ['id:file'] * 4)
        self.assertEqual(sdk.files_search_continue_v2.call_count, 4)
        self.assertIsNotNone(result['next_cursor'])

    def test_search_buffers_provider_overrun_without_skipping_ranked_items(self) -> None:
        """Resume over-returned provider matches by current metadata before continuing."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _folder('Projects', 'id:root', '/Projects'),
            _folder('Projects', 'id:root', '/Projects'),
            _file('second.txt', 'id:second', '/Projects/second.txt'),
        ]
        first_item = _file('first.txt', 'id:first', '/Projects/first.txt')
        second_item = _file('second.txt', 'id:second', '/Projects/second.txt')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: first_item)),
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: second_item)),
            ],
            has_more=True,
            cursor='provider-next',
        )
        client = self._client(sdk)
        first = client.search(root='projects', query='txt', max_results=1)
        resumed = client.search(root='projects', query='txt', cursor=first['next_cursor'], max_results=1)
        self.assertEqual([item['id'] for item in first['items']], ['id:first'])
        self.assertEqual([item['id'] for item in resumed['items']], ['id:second'])
        sdk.files_search_continue_v2.assert_not_called()

    def test_search_discards_outside_candidate_from_provider_page(self) -> None:
        """Skip one stale outside candidate while preserving provider-ranked results."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        outside = _file('outside.txt', 'id:outside', '/Elsewhere/outside.txt')
        inside = _file('inside.txt', 'id:inside', '/Projects/inside.txt')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: outside)),
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: inside)),
            ],
            has_more=False,
            cursor='unused',
        )

        result = self._client(sdk).search(root='projects', query='txt', max_results=2)

        self.assertEqual([item['id'] for item in result['items']], ['id:inside'])
        self.assertIsNone(result['next_cursor'])

    def test_search_discards_moved_buffered_candidate_and_continues(self) -> None:
        """Skip a moved buffered item and continue from the preserved provider token."""
        sdk = MagicMock()
        root = _folder('Projects', 'id:root', '/Projects')
        sdk.files_get_metadata.side_effect = [
            root,
            root,
            _file('second.txt', 'id:second', '/Elsewhere/second.txt'),
        ]
        first = _file('first.txt', 'id:first', '/Projects/first.txt')
        second = _file('second.txt', 'id:second', '/Projects/second.txt')
        third = _file('third.txt', 'id:third', '/Projects/third.txt')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: first)),
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: second)),
            ],
            has_more=True,
            cursor='provider-next',
        )
        sdk.files_search_continue_v2.return_value = SimpleNamespace(
            matches=[SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: third))],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)
        page_one = client.search(root='projects', query='txt', max_results=1)

        page_two = client.search(root='projects', query='txt', cursor=page_one['next_cursor'], max_results=1)

        self.assertEqual([item['id'] for item in page_one['items']], ['id:first'])
        self.assertEqual([item['id'] for item in page_two['items']], ['id:third'])
        sdk.files_search_continue_v2.assert_called_once_with('provider-next')

    def test_search_exhausts_filtered_buffer_without_invalid_continuation(self) -> None:
        """Finish cleanly when buffered metadata changes kind and no provider page remains."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _folder('Projects', 'id:root', '/Projects'),
            _folder('Projects', 'id:root', '/Projects'),
            _folder('second', 'id:second', '/Projects/second'),
        ]
        first_item = _file('first.txt', 'id:first', '/Projects/first.txt')
        second_item = _file('second.txt', 'id:second', '/Projects/second.txt')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: first_item)),
                SimpleNamespace(metadata=SimpleNamespace(get_metadata=lambda: second_item)),
            ],
            has_more=False,
            cursor='unused',
        )
        client = self._client(sdk)
        first = client.search(root='projects', query='txt', kinds=('file',), max_results=1)
        resumed = client.search(
            root='projects',
            query='txt',
            kinds=('file',),
            cursor=first['next_cursor'],
            max_results=1,
        )
        self.assertEqual(resumed, {'items': [], 'next_cursor': None})
        sdk.files_search_continue_v2.assert_not_called()

    def test_cursor_mismatches_and_strict_types_fail_before_sdk_construction(self) -> None:
        """Reject malformed or rebound cursor envelopes before resolving credentials."""
        seed_sdk = MagicMock()
        seed_sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        seed_sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[_file('a', 'id:a', '/Projects/a')],
            has_more=True,
            cursor='provider',
        )
        cursor = self._client(seed_sdk).list_folder(root='projects', max_results=1)['next_cursor']
        for altered in (
            {'instance': 'other'},
            {'root': 'other'},
            {'root_locator': '/Other'},
            {'operation': 'search'},
            {'v': True},
            {'resource_ref': 'id:other'},
        ):
            payload = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            payload.update(altered)
            forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
            factory = MagicMock()
            client = DropboxClient(
                token_supplier=_credential,
                config=_valid_config(),
                instance_id='dbx',
                sdk_factory=factory,
            )
            with self.subTest(altered=altered), self.assertRaises(DropboxInvalidCursorError):
                client.list_folder(root='projects', cursor=forged, max_results=1)
            factory.assert_not_called()

    def test_oversized_cursor_envelopes_and_states_fail_before_sdk_construction(self) -> None:
        """Bound encoded cursors, provider state, pending refs, and individual refs."""
        seed_sdk = MagicMock()
        seed_sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        seed_sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[_file('a', 'id:a', '/Projects/a')],
            has_more=True,
            cursor='provider',
        )
        valid_cursor = self._client(seed_sdk).list_folder(root='projects', max_results=1)['next_cursor']
        payload = json.loads(base64.urlsafe_b64decode(valid_cursor + '=' * (-len(valid_cursor) % 4)))
        oversized_states = (
            'x' * 100_000,
            json.dumps({'cursor': 'x' * 20_000, 'pending': []}),
            json.dumps({'cursor': None, 'pending': [f'id:{index}' for index in range(1_001)]}),
            json.dumps({'cursor': None, 'pending': ['x' * 10_000]}),
        )
        cursors = ['A' * 200_000]
        for state in oversized_states:
            forged_payload = dict(payload)
            forged_payload['provider_cursor'] = state
            cursors.append(base64.urlsafe_b64encode(json.dumps(forged_payload).encode()).rstrip(b'=').decode())

        for cursor in cursors:
            factory = MagicMock()
            client = DropboxClient(
                token_supplier=_credential,
                config=_valid_config(),
                instance_id='dbx',
                sdk_factory=factory,
            )
            with self.subTest(length=len(cursor)), self.assertRaises(DropboxInvalidCursorError):
                client.list_folder(root='projects', cursor=cursor, max_results=1)
            factory.assert_not_called()

    def test_oversized_provider_pages_fail_without_unbounded_processing(self) -> None:
        """Reject provider pages beyond the processing budget instead of buffering them."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        entry = _file('a', 'id:a', '/Projects/a')
        sdk.files_list_folder.return_value = SimpleNamespace(
            entries=[entry] * 1_001,
            has_more=False,
            cursor='unused',
        )
        with self.assertRaises(DropboxAPIError):
            self._client(sdk).list_folder(root='projects', max_results=1)

    def test_search_rejects_oversized_generated_cursor_binding(self) -> None:
        """Reject a query beyond the accepted provider limit before SDK access."""
        sdk = MagicMock()
        sdk.files_get_metadata.return_value = _folder('Projects', 'id:root', '/Projects')
        sdk.files_search_v2.return_value = SimpleNamespace(
            matches=[],
            has_more=True,
            cursor='provider',
        )
        with self.assertRaises(DropboxConfigError):
            self._client(sdk).search(root='projects', query='x' * 10_000, max_results=1)
        sdk.files_get_metadata.assert_not_called()

    def test_authorization_uses_provider_unicode_path_lower_verbatim(self) -> None:
        """Never apply modern Python Unicode casing to provider-authoritative paths."""
        sdk = MagicMock()
        sdk.files_get_metadata.side_effect = [
            _folder('Legacy', 'id:root', '/Legacy', path_lower='/ꙋ'),
            _file('item', 'id:item', '/Legacy/item', path_lower='/Ꙋ/item'),
        ]
        with self.assertRaises(DropboxOutsideRootError):
            self._client(sdk, config={'roots': [{'id': 'legacy', 'path': '/Legacy'}]}).get_metadata(
                root='legacy',
                item_ref='id:item',
            )
