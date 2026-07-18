# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the Django-free Google Drive metadata client foundation."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError
from libs.clients.google_drive.client import (
    _FOLDER_MIME_TYPE,
    _SHORTCUT_MIME_TYPE,
    DRIVE_FIELDS,
    DRIVE_METADATA_SCOPE,
    GoogleDriveClient,
    _build_service,
)
from libs.clients.google_drive.config import (
    GoogleDriveConfig,
    GoogleDriveRoot,
    parse_google_drive_config,
)
from libs.clients.google_drive.errors import (
    GoogleDriveAPIError,
    GoogleDriveAuthError,
    GoogleDriveConfigError,
    GoogleDriveForbiddenError,
    GoogleDriveInvalidCursorError,
    GoogleDriveNotFoundError,
    GoogleDriveOutsideRootError,
    GoogleDriveRateLimitedError,
)

from olib.py.django.test.cases import OTestCase

# Foundation behavior is intentionally exercised before Task 4 exposes public operations.
# pylint: disable=protected-access


def _valid_config(**overrides: object) -> dict[str, object]:
    """Return valid non-secret Drive configuration with optional replacements."""
    config: dict[str, object] = {
        'roots': [{'id': 'approved', 'file_id': 'folder-1'}],
    }
    config.update(overrides)
    return config


def _http_failure(
    status: int,
    *,
    reason: str | None = None,
    retry_after: str | None = None,
) -> HttpError:
    """Build a provider failure with controlled status, reason, and retry header."""
    response = MagicMock()
    response.status = status
    response.get.side_effect = lambda name, default=None: (
        retry_after if name.lower() == 'retry-after' and retry_after is not None else default
    )
    content = (
        f'{{"error":{{"errors":[{{"reason":"{reason}"}}]}}}}'.encode()
        if reason is not None
        else b'provider-private-response'
    )
    return HttpError(response, content)


def _client_traceback_locals(exc: BaseException) -> list[tuple[str, dict[str, object]]]:
    """Collect locals retained by Google Drive client frames in an exception traceback."""
    retained: list[tuple[str, dict[str, object]]] = []
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith('/libs/clients/google_drive/client.py'):
            retained.append(
                (
                    traceback.tb_frame.f_code.co_name,
                    dict(traceback.tb_frame.f_locals),
                )
            )
        traceback = traceback.tb_next
    return retained


class TestGoogleDriveConfig(OTestCase):
    """Validate immutable Drive addressing before any provider call."""

    def test_requires_nonempty_roots_list(self) -> None:
        """Reject omitted, empty, and non-list roots."""
        configs: tuple[dict[str, Any], ...] = (
            {},
            {'roots': []},
            {'roots': ()},
            {'roots': 'root'},
        )
        for config in configs:
            with self.subTest(config=config), self.assertRaises(GoogleDriveConfigError):
                parse_google_drive_config(config)

    def test_rejects_malformed_root_entries(self) -> None:
        """Reject non-mappings and blank or non-string aliases and file IDs."""
        malformed: tuple[dict[str, Any], ...] = (
            {'roots': [None]},
            {'roots': [{}]},
            {'roots': [{'id': '', 'file_id': 'file'}]},
            {'roots': [{'id': 1, 'file_id': 'file'}]},
            {'roots': [{'id': 'alias', 'file_id': '  '}]},
            {'roots': [{'id': 'alias', 'file_id': 2}]},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(GoogleDriveConfigError):
                parse_google_drive_config(config)

    def test_rejects_duplicate_aliases_and_file_ids(self) -> None:
        """Require aliases and provider locators to be unique after trimming."""
        duplicates = (
            {
                'roots': [
                    {'id': 'same', 'file_id': 'one'},
                    {'id': ' same ', 'file_id': 'two'},
                ]
            },
            {
                'roots': [
                    {'id': 'one', 'file_id': 'same'},
                    {'id': 'two', 'file_id': ' same '},
                ]
            },
        )
        for config in duplicates:
            with self.subTest(config=config), self.assertRaises(GoogleDriveConfigError):
                parse_google_drive_config(config)

    def test_defaults_to_user_corpus_and_strips_values(self) -> None:
        """Return immutable records with normalized aliases and user corpus."""
        parsed = parse_google_drive_config(
            {
                'subject': ' user@example.com ',
                'roots': [{'id': ' approved ', 'file_id': ' folder-1 '}],
            }
        )
        self.assertEqual(
            parsed,
            GoogleDriveConfig(
                subject='user@example.com',
                roots=(
                    GoogleDriveRoot(
                        id='approved',
                        file_id='folder-1',
                        corpus='user',
                    ),
                ),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            parsed.subject = 'other@example.com'  # type: ignore[misc]

    def test_drive_id_implies_drive_corpus(self) -> None:
        """Select the Shared Drive corpus when a drive ID is provided."""
        parsed = parse_google_drive_config(
            {
                'roots': [
                    {
                        'id': 'company',
                        'file_id': 'folder-1',
                        'drive_id': ' drive-1 ',
                    }
                ]
            }
        )
        self.assertEqual(parsed.roots[0].corpus, 'drive')
        self.assertEqual(parsed.roots[0].drive_id, 'drive-1')

    def test_drive_corpus_requires_drive_id(self) -> None:
        """Reject a Shared Drive corpus without a usable drive locator."""
        for drive_id in (None, '', '  ', 1):
            root: dict[str, Any] = {
                'id': 'company',
                'file_id': 'folder-1',
                'corpus': 'drive',
            }
            if drive_id is not None:
                root['drive_id'] = drive_id
            with self.subTest(drive_id=drive_id), self.assertRaises(GoogleDriveConfigError):
                parse_google_drive_config({'roots': [root]})

    def test_rejects_unknown_values_and_fields(self) -> None:
        """Reject unsupported corpus names, fields, and malformed subjects."""
        malformed = (
            {'roots': [{'id': 'a', 'file_id': 'b', 'corpus': 'all'}]},
            {'roots': [{'id': 'a', 'file_id': 'b', 'corpus': 'user', 'drive_id': 'd'}]},
            {'roots': [{'id': 'a', 'file_id': 'b', 'extra': True}]},
            {'roots': [{'id': 'a', 'file_id': 'b'}], 'extra': True},
            {'roots': [{'id': 'a', 'file_id': 'b'}], 'subject': 1},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(GoogleDriveConfigError):
                parse_google_drive_config(config)

    def test_invalid_config_does_not_resolve_credentials_or_build_service(self) -> None:
        """Fail structural parsing without touching either provider boundary."""
        supplier = MagicMock()
        factory = MagicMock()
        with self.assertRaises(GoogleDriveConfigError):
            GoogleDriveClient(
                token_supplier=supplier,
                config={'roots': []},
                instance_id='drive',
                service_factory=factory,
            )
        supplier.assert_not_called()
        factory.assert_not_called()


class TestGoogleDriveAuth(OTestCase):
    """Verify just-in-time Drive-only service construction."""

    def test_non_delegated_factory_receives_none_subject(self) -> None:
        """Pass plaintext to the operation-local factory without adding delegation."""
        service = MagicMock()
        calls: list[tuple[str, str | None]] = []

        def factory(raw: str, subject: str | None) -> object:
            """Capture the injected service-factory boundary."""
            calls.append((raw, subject))
            return service

        client = GoogleDriveClient(
            token_supplier=lambda: '{"type":"service_account"}',
            config=_valid_config(),
            instance_id='drive',
            service_factory=factory,
        )
        self.assertIs(client._service(), service)
        self.assertEqual(calls, [('{"type":"service_account"}', None)])

    def test_delegated_factory_receives_nonempty_subject(self) -> None:
        """Pass the normalized delegated subject to the operation-local factory."""
        calls: list[tuple[str, str | None]] = []

        def factory(raw: str, subject: str | None) -> object:
            """Capture delegated service construction."""
            calls.append((raw, subject))
            return MagicMock()

        client = GoogleDriveClient(
            token_supplier=lambda: '{"type":"service_account"}',
            config=_valid_config(subject=' user@example.com '),
            instance_id='drive',
            service_factory=factory,
        )
        client._service()
        self.assertEqual(calls, [('{"type":"service_account"}', 'user@example.com')])

    @patch('googleapiclient.discovery.build')
    @patch('google.oauth2.service_account.Credentials.from_service_account_info')
    def test_default_factory_uses_exact_scope_and_drive_build_args(
        self,
        from_info: MagicMock,
        build: MagicMock,
    ) -> None:
        """Use only metadata scope and construct Drive v3 without discovery caching."""
        credentials = MagicMock()
        delegated = MagicMock()
        credentials.with_subject.return_value = delegated
        from_info.return_value = credentials
        info = {'type': 'service_account', 'client_email': 'sa@example.com'}

        _build_service(
            '{"type":"service_account","client_email":"sa@example.com"}',
            'user@example.com',
        )

        from_info.assert_called_once_with(info, scopes=(DRIVE_METADATA_SCOPE,))
        credentials.with_subject.assert_called_once_with('user@example.com')
        build.assert_called_once_with(
            'drive',
            'v3',
            credentials=delegated,
            cache_discovery=False,
        )

    @patch('googleapiclient.discovery.build')
    @patch('google.oauth2.service_account.Credentials.from_service_account_info')
    def test_default_factory_skips_delegation_without_subject(
        self,
        from_info: MagicMock,
        build: MagicMock,
    ) -> None:
        """Do not call with_subject for the service-account identity."""
        credentials = MagicMock()
        from_info.return_value = credentials
        _build_service('{"type":"service_account"}', None)
        credentials.with_subject.assert_not_called()
        build.assert_called_once_with(
            'drive',
            'v3',
            credentials=credentials,
            cache_discovery=False,
        )

    def test_missing_credential_maps_to_auth_failure(self) -> None:
        """Reject an absent plaintext credential only when service creation begins."""
        client = GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
        )
        with self.assertRaisesMessage(
            GoogleDriveAuthError,
            'no Google service-account credential resolved',
        ):
            client._service()

    def test_malformed_credential_maps_to_safe_auth_failure(self) -> None:
        """Reject malformed JSON without retaining the parser failure or its secret."""
        credential = 'private malformed credential'
        try:
            _build_service(credential, None)
        except GoogleDriveAuthError as failure:
            frames = _client_traceback_locals(failure)
            self.assertNotIn(credential, str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
            self.assertTrue(failure.__suppress_context__)
            self.assertEqual({name for name, _ in frames}, {'_build_service'})
            self.assertNotIn(credential, repr(frames))
        else:
            self.fail('malformed credential did not raise GoogleDriveAuthError')

    def test_service_auth_failure_clears_raw_and_parsed_credential_locals(self) -> None:
        """Remove every credential-bearing value from retained production frames."""
        private_key = 'RAW-PRIVATE-KEY-UNIQUE-SENTINEL'
        parsed_secret = 'PARSED-KEY-ID-UNIQUE-SENTINEL'
        credential = (
            '{"type":"service_account","project_id":"project",'
            f'"private_key_id":"{parsed_secret}","private_key":"{private_key}",'
            '"client_email":"sa@example.com","client_id":"123",'
            '"token_uri":"https://oauth2.googleapis.com/token"}'
        )
        client = GoogleDriveClient(
            token_supplier=lambda: credential,
            config=_valid_config(),
            instance_id='drive',
        )
        try:
            client._service()
        except GoogleDriveAuthError as failure:
            frames = _client_traceback_locals(failure)
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
            retained = repr(frames)
            frame_names = {name for name, _ in frames}
            self.assertEqual(frame_names, {'_service', '_build_service'})
            self.assertNotIn(credential, retained)
            self.assertNotIn(private_key, retained)
            self.assertNotIn(parsed_secret, retained)
        else:
            self.fail('invalid service-account credential did not raise GoogleDriveAuthError')

    def test_constructor_retains_only_supplier_and_nonsecret_config(self) -> None:
        """Avoid resolving or retaining plaintext credentials and built services."""
        supplier = MagicMock(return_value='plaintext')
        factory = MagicMock()
        client = GoogleDriveClient(
            token_supplier=supplier,
            config=_valid_config(),
            instance_id='drive',
            service_factory=factory,
        )
        supplier.assert_not_called()
        factory.assert_not_called()
        self.assertNotIn('plaintext', repr(vars(client)))
        self.assertNotIn('service', {key.removeprefix('_') for key in vars(client)})


class TestGoogleDriveNormalization(OTestCase):
    """Verify the metadata-only normalized response shape."""

    def test_normalizes_folder_file_and_shortcut_metadata(self) -> None:
        """Map provider MIME types and values into stable metadata records."""
        client = GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
        )
        folder = client._normalize_item(
            {
                'id': 'folder',
                'name': 'Folder',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': ['root'],
            },
            root_alias='approved',
        )
        file_item = client._normalize_item(
            {
                'id': 'file',
                'name': 'File',
                'mimeType': 'text/plain',
                'size': '42',
                'modifiedTime': '2026-07-18T12:00:00Z',
                'webViewLink': 'https://drive.google.com/file',
                'driveId': 'drive-1',
            },
            root_alias='approved',
        )
        shortcut = client._normalize_item(
            {
                'id': 'shortcut',
                'name': 'Shortcut',
                'mimeType': 'application/vnd.google-apps.shortcut',
                'shortcutDetails': {
                    'targetId': 'secret-target',
                    'targetMimeType': 'application/pdf',
                },
            },
            root_alias='approved',
        )
        self.assertEqual(folder['kind'], 'folder')
        self.assertIsNone(folder['size'])
        self.assertEqual(file_item['kind'], 'file')
        self.assertEqual(file_item['size'], 42)
        self.assertEqual(file_item['provider_metadata'], {'drive_id': 'drive-1'})
        self.assertEqual(shortcut['kind'], 'shortcut')
        self.assertEqual(
            shortcut['provider_metadata'],
            {'shortcut_target_mime_type': 'application/pdf'},
        )
        self.assertNotIn('secret-target', repr(shortcut))

    def test_normalized_shape_has_nullable_path_and_web_url(self) -> None:
        """Return explicit nulls when Drive does not provide paths or browser links."""
        client = GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
        )
        item = client._normalize_item(
            {'id': 'file', 'name': 'File', 'mimeType': 'text/plain'},
            root_alias='approved',
        )
        self.assertEqual(
            item,
            {
                'provider': 'google_drive',
                'root': 'approved',
                'id': 'file',
                'name': 'File',
                'kind': 'file',
                'mime_type': 'text/plain',
                'size': None,
                'modified_at': None,
                'parent_refs': [],
                'path': None,
                'web_url': None,
                'provider_metadata': {},
            },
        )

    def test_fields_and_output_exclude_nonmetadata_capabilities(self) -> None:
        """Never request or serialize content, export, thumbnail, permission, or download data."""
        forbidden = {
            'content',
            'exportLinks',
            'thumbnailLink',
            'permissions',
            'downloadUrl',
        }
        self.assertTrue(forbidden.isdisjoint(DRIVE_FIELDS.split(',')))
        client = GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
        )
        item = client._normalize_item(
            {
                'id': 'file',
                'name': 'File',
                'mimeType': 'text/plain',
                **{key: 'private' for key in forbidden},
            },
            root_alias='approved',
        )
        self.assertTrue(forbidden.isdisjoint(item))
        self.assertNotIn('private', repr(item))

    def test_max_results_bounds(self) -> None:
        """Accept provider page sizes from one through one hundred only."""
        client = GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
        )
        self.assertEqual(client._validate_max_results(1), 1)
        self.assertEqual(client._validate_max_results(100), 100)
        for value in (0, 101, True, 1.5):
            with self.subTest(value=value), self.assertRaises(GoogleDriveConfigError):
                client._validate_max_results(value)  # type: ignore[arg-type]


class TestGoogleDriveExecution(OTestCase):
    """Verify bounded retries and safe typed provider failures."""

    def _client(self, *, sleeps: list[float] | None = None) -> GoogleDriveClient:
        """Build a client with an observable sleep boundary."""
        observed = sleeps if sleeps is not None else []
        return GoogleDriveClient(
            token_supplier=lambda: None,
            config=_valid_config(),
            instance_id='drive',
            sleep_fn=observed.append,
        )

    def test_retries_429_once_and_honors_numeric_retry_after(self) -> None:
        """Retry one quota response using its numeric provider delay."""
        request = MagicMock()
        request.execute.side_effect = [_http_failure(429, retry_after='2.5'), {'id': 'ok'}]
        sleeps: list[float] = []
        result = self._client(sleeps=sleeps)._execute(request, operation='get metadata')
        self.assertEqual(result, {'id': 'ok'})
        self.assertEqual(request.execute.call_count, 2)
        self.assertEqual(sleeps, [2.5])

    def test_retry_after_rejects_nonfinite_and_nonpositive_values(self) -> None:
        """Replace unsafe provider delays with a zero-second bounded retry."""
        for value in ('inf', '-inf', 'nan', '0', '-1'):
            request = MagicMock()
            request.execute.side_effect = [
                _http_failure(429, retry_after=value),
                {'id': 'ok'},
            ]
            sleeps: list[float] = []
            with self.subTest(value=value):
                self._client(sleeps=sleeps)._execute(request, operation='get metadata')
                self.assertEqual(sleeps, [0.0])

    def test_retry_after_caps_large_numeric_values(self) -> None:
        """Cap a valid but excessive provider delay at sixty seconds."""
        request = MagicMock()
        request.execute.side_effect = [
            _http_failure(429, retry_after='3600'),
            {'id': 'ok'},
        ]
        sleeps: list[float] = []
        self._client(sleeps=sleeps)._execute(request, operation='get metadata')
        self.assertEqual(sleeps, [60.0])

    def test_retries_transient_server_failure_once(self) -> None:
        """Retry a transient 5xx once before returning the successful result."""
        request = MagicMock()
        request.execute.side_effect = [_http_failure(503), {'id': 'ok'}]
        sleeps: list[float] = []
        result = self._client(sleeps=sleeps)._execute(request, operation='list metadata')
        self.assertEqual(result, {'id': 'ok'})
        self.assertEqual(request.execute.call_count, 2)
        self.assertEqual(sleeps, [0.0])

    def test_exhausted_429_maps_to_rate_limited(self) -> None:
        """Raise a typed quota failure without retaining provider response content."""
        request = MagicMock()
        request.execute.side_effect = [_http_failure(429), _http_failure(429)]
        with self.assertRaises(GoogleDriveRateLimitedError) as caught:
            self._client()._execute(request, operation='search metadata')
        self.assertNotIn('provider-private-response', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_quota_reason_on_403_maps_to_rate_limited_without_retry(self) -> None:
        """Recognize Google quota reasons while leaving ordinary 403 responses distinct."""
        request = MagicMock()
        request.execute.side_effect = _http_failure(403, reason='rateLimitExceeded')
        with self.assertRaises(GoogleDriveRateLimitedError):
            self._client()._execute(request, operation='search metadata')
        request.execute.assert_called_once()

    def test_quota_exceeded_on_403_maps_to_rate_limited(self) -> None:
        """Recognize Google's generic quota exhaustion reason."""
        request = MagicMock()
        request.execute.side_effect = _http_failure(403, reason='quotaExceeded')
        with self.assertRaises(GoogleDriveRateLimitedError):
            self._client()._execute(request, operation='search metadata')

    def test_storage_quota_exceeded_on_403_maps_to_rate_limited(self) -> None:
        """Recognize Google's storage quota exhaustion reason."""
        request = MagicMock()
        request.execute.side_effect = _http_failure(403, reason='storageQuotaExceeded')
        with self.assertRaises(GoogleDriveRateLimitedError):
            self._client()._execute(request, operation='search metadata')

    def test_statuses_map_to_typed_safe_failures(self) -> None:
        """Map auth, permission, missing, and remaining statuses without response content."""
        cases = (
            (401, GoogleDriveAuthError),
            (403, GoogleDriveForbiddenError),
            (404, GoogleDriveNotFoundError),
            (418, GoogleDriveAPIError),
        )
        for status, expected in cases:
            request = MagicMock()
            request.execute.side_effect = _http_failure(status)
            with self.subTest(status=status), self.assertRaises(expected) as caught:
                self._client()._execute(request, operation='get metadata')
            self.assertNotIn('provider-private-response', str(caught.exception))
            if isinstance(caught.exception, GoogleDriveAPIError):
                self.assertEqual(caught.exception.status, status)


class _DriveRequest:
    """Return one preconfigured provider response when executed."""

    def __init__(self, response: dict[str, Any]) -> None:
        """Store the response without provider behavior."""
        self._response = response

    def execute(self) -> dict[str, Any]:
        """Return the configured response."""
        return self._response


class _DriveFiles:
    """Record Drive files.get/list calls against mutable test metadata."""

    def __init__(
        self,
        items: dict[str, dict[str, Any]],
        pages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Use mutable items and queued pages so moves and resumes are observable."""
        self.items = items
        self.pages = list(pages or [])
        self.get_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> _DriveRequest:
        """Record and return current item metadata."""
        self.get_calls.append(kwargs)
        return _DriveRequest(dict(self.items[kwargs['fileId']]))

    def list(self, **kwargs: Any) -> _DriveRequest:
        """Record and return the next provider page."""
        self.list_calls.append(kwargs)
        return _DriveRequest(self.pages.pop(0))


class _DrivePagedFiles(_DriveFiles):
    """Model provider pagination that strictly honors each requested page size."""

    def __init__(
        self,
        items: dict[str, dict[str, Any]],
        search_items: list[dict[str, Any]],
    ) -> None:
        """Store a ranked provider result stream addressed by offset tokens."""
        super().__init__(items)
        self.search_items = search_items

    def list(self, **kwargs: Any) -> _DriveRequest:
        """Slice the provider stream using pageSize and an opaque offset token."""
        self.list_calls.append(kwargs)
        offset = int(kwargs.get('pageToken', '0'))
        page_size = kwargs['pageSize']
        end = min(offset + page_size, len(self.search_items))
        response: dict[str, Any] = {'files': self.search_items[offset:end]}
        if end < len(self.search_items):
            response['nextPageToken'] = str(end)
        return _DriveRequest(response)


class _DriveService:
    """Expose one files resource for an operation-local fake service."""

    def __init__(self, files: _DriveFiles) -> None:
        """Retain the fake files resource."""
        self._files = files

    def files(self) -> _DriveFiles:
        """Return the fake Drive files resource."""
        return self._files


class TestGoogleDriveOperations(OTestCase):
    """Exercise root-safe production metadata operations at the API boundary."""

    def _client(
        self,
        files: _DriveFiles,
        *,
        config: dict[str, Any] | None = None,
        instance_id: str = 'drive',
        builds: list[bool] | None = None,
    ) -> GoogleDriveClient:
        """Build a client whose operation-local services share observable provider state."""
        observed = builds if builds is not None else []

        def factory(_raw: str, _subject: str | None) -> _DriveService:
            """Record one operation-local service construction."""
            observed.append(True)
            return _DriveService(files)

        return GoogleDriveClient(
            token_supplier=lambda: '{}',
            config=config or {'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id=instance_id,
            service_factory=factory,
        )

    def test_list_roots_fetches_only_configured_roots_and_canonicalizes_root(self) -> None:
        """Resolve every configured locator once and expose canonical root metadata."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'canonical', 'name': 'My Drive', 'mimeType': _FOLDER_MIME_TYPE},
            'shared': {'id': 'shared', 'name': 'Shared', 'mimeType': 'text/plain'},
        }
        files = _DriveFiles(items)
        client = self._client(
            files,
            config={
                'roots': [
                    {'id': 'mine', 'file_id': 'root'},
                    {'id': 'brief', 'file_id': 'shared'},
                ]
            },
        )

        result = client.list_roots()

        self.assertEqual([item['id'] for item in result['items']], ['canonical', 'shared'])
        self.assertEqual([item['root'] for item in result['items']], ['mine', 'brief'])
        self.assertEqual([call['fileId'] for call in files.get_calls], ['root', 'shared'])
        self.assertTrue(all(call['supportsAllDrives'] for call in files.get_calls))

    def test_list_folder_uses_direct_parent_user_corpus_and_ordering(self) -> None:
        """List direct children with deterministic ordering in the user corpus."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'canonical', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {
                            'id': 'child',
                            'name': 'Child',
                            'mimeType': 'text/plain',
                            'parents': ['canonical'],
                        }
                    ]
                }
            ],
        )

        result = self._client(
            files,
            config={'roots': [{'id': 'mine', 'file_id': 'root'}]},
        ).list_folder(root='mine')

        self.assertEqual([item['id'] for item in result['items']], ['child'])
        call = files.list_calls[0]
        self.assertEqual(call['q'], "'canonical' in parents and trashed = false")
        self.assertEqual(call['corpora'], 'user')
        self.assertEqual(call['orderBy'], 'folder,name_natural')
        self.assertEqual(call['pageSize'], 50)
        self.assertTrue(call['supportsAllDrives'])
        self.assertTrue(call['includeItemsFromAllDrives'])
        self.assertNotIn('driveId', call)

    def test_list_folder_uses_shared_drive_location_flags(self) -> None:
        """Select the configured Shared Drive corpus and locator."""
        items = {
            'folder': {'id': 'folder', 'name': 'Folder', 'mimeType': _FOLDER_MIME_TYPE},
        }
        files = _DriveFiles(items, pages=[{'files': []}])
        self._client(
            files,
            config={
                'roots': [
                    {
                        'id': 'company',
                        'file_id': 'folder',
                        'drive_id': 'drive-1',
                    }
                ]
            },
        ).list_folder(root='company', max_results=7)
        call = files.list_calls[0]
        self.assertEqual(call['corpora'], 'drive')
        self.assertEqual(call['driveId'], 'drive-1')
        self.assertEqual(call['pageSize'], 7)
        self.assertTrue(call['supportsAllDrives'])
        self.assertTrue(call['includeItemsFromAllDrives'])

    def test_public_input_bounds_fail_before_service_construction(self) -> None:
        """Reject oversized aliases, refs, queries, and kind lists before credentials."""
        builds: list[bool] = []
        client = self._client(_DriveFiles({}), builds=builds)
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.list_folder(root='x' * 257),
            lambda: client.list_folder(root='approved', folder_ref='x' * 4_097),
            lambda: client.get_metadata(root='approved', item_ref='x' * 4_097),
            lambda: client.search(root='approved', query='x' * 4_097),
            lambda: client.search(root='approved', query='x', kinds=('file', 'folder', 'file')),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(GoogleDriveConfigError):
                operation()
        self.assertEqual(builds, [])

    def test_list_folder_buffers_provider_overrun_and_reauthorizes_resume(self) -> None:
        """Cap over-returned pages and refetch buffered IDs before returning them."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'c': {'id': 'c', 'name': 'C', 'mimeType': 'text/plain', 'parents': ['root']},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['root']},
                        {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['root']},
                        dict(items['c']),
                    ]
                }
            ],
        )
        client = self._client(files)

        first = client.list_folder(root='approved', max_results=2)
        resumed = client.list_folder(root='approved', cursor=first['next_cursor'], max_results=2)

        self.assertEqual([item['id'] for item in first['items']], ['a', 'b'])
        self.assertEqual([item['id'] for item in resumed['items']], ['c'])
        self.assertIsNone(resumed['next_cursor'])
        self.assertEqual([call['fileId'] for call in files.get_calls].count('c'), 1)
        self.assertNotIn('name', first['next_cursor'])

    def test_list_folder_continuation_overrun_returns_all_items_once(self) -> None:
        """Buffer a continuation tail without skipping or duplicating provider order."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'd': {'id': 'd', 'name': 'D', 'mimeType': 'text/plain', 'parents': ['root']},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [{'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['root']}],
                    'nextPageToken': 'next',
                },
                {
                    'files': [
                        {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['root']},
                        {'id': 'c', 'name': 'C', 'mimeType': 'text/plain', 'parents': ['root']},
                        dict(items['d']),
                    ]
                },
            ],
        )
        client = self._client(files)
        pages: list[dict[str, Any]] = []
        cursor = None
        while not pages or cursor is not None:
            page = client.list_folder(root='approved', cursor=cursor, max_results=2)
            pages.append(page)
            cursor = page['next_cursor']

        returned = [item['id'] for page in pages for item in page['items']]
        self.assertEqual(returned, ['a', 'b', 'c', 'd'])
        self.assertEqual(len(returned), len(set(returned)))
        self.assertEqual(files.list_calls[1]['pageToken'], 'next')

    def test_list_folder_rejects_moved_buffered_item_on_resume(self) -> None:
        """Recheck buffered metadata against both root and selected direct folder."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'outside': {'id': 'outside', 'parents': []},
            'b': {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['root']},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['root']},
                        dict(items['b']),
                    ]
                }
            ],
        )
        client = self._client(files)
        first = client.list_folder(root='approved', max_results=1)
        items['b']['parents'] = ['outside']

        with self.assertRaises(GoogleDriveOutsideRootError):
            client.list_folder(root='approved', cursor=first['next_cursor'], max_results=1)

    def test_list_folder_rejects_provider_page_beyond_processing_bound(self) -> None:
        """Reject a provider overrun larger than the bounded metadata-ID budget."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        entry = {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['root']}
        files = _DriveFiles(items, pages=[{'files': [entry] * 501}])

        with self.assertRaises(GoogleDriveAPIError):
            self._client(files).list_folder(root='approved', max_results=1)

    def test_list_cursor_rejects_cross_folder_reuse_before_provider_calls(self) -> None:
        """Bind list cursors to the explicit folder selected by the originating call."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'folder-a': {
                'id': 'folder-a',
                'name': 'A',
                'mimeType': _FOLDER_MIME_TYPE,
                'parents': ['root'],
            },
            'folder-b': {
                'id': 'folder-b',
                'name': 'B',
                'mimeType': _FOLDER_MIME_TYPE,
                'parents': ['root'],
            },
        }
        first_files = _DriveFiles(items, pages=[{'files': [], 'nextPageToken': 'next'}])
        cursor = self._client(first_files).list_folder(
            root='approved',
            folder_ref='folder-a',
        )['next_cursor']
        resumed_files = _DriveFiles(items)

        with self.assertRaises(GoogleDriveInvalidCursorError):
            self._client(resumed_files).list_folder(
                root='approved',
                folder_ref='folder-b',
                cursor=cursor,
            )

        self.assertEqual(resumed_files.get_calls, [])
        self.assertEqual(resumed_files.list_calls, [])

    def test_list_default_folder_cursor_rejects_explicit_reuse_before_provider_calls(self) -> None:
        """Keep canonical-root default cursors distinct from explicit folder calls."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        first_files = _DriveFiles(items, pages=[{'files': [], 'nextPageToken': 'next'}])
        cursor = self._client(first_files).list_folder(root='approved')['next_cursor']
        resumed_files = _DriveFiles(items)

        with self.assertRaises(GoogleDriveInvalidCursorError):
            self._client(resumed_files).list_folder(
                root='approved',
                folder_ref='root',
                cursor=cursor,
            )

        self.assertEqual(resumed_files.get_calls, [])

    def test_list_default_folder_cursor_resumes_with_canonical_root_id(self) -> None:
        """Resolve a special root locator before matching its canonical cursor binding."""
        items = {'root': {'id': 'canonical', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        first_files = _DriveFiles(items, pages=[{'files': [], 'nextPageToken': 'next'}])
        cursor = self._client(first_files).list_folder(root='approved')['next_cursor']
        resumed_files = _DriveFiles(items, pages=[{'files': []}])

        self._client(resumed_files).list_folder(root='approved', cursor=cursor)

        self.assertEqual(resumed_files.list_calls[0]['pageToken'], 'next')

    def test_get_metadata_walks_current_parents_and_rejects_after_move(self) -> None:
        """Authorize arbitrary references only while current ancestry reaches the root."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'folder': {'id': 'folder', 'parents': ['root']},
            'file': {'id': 'file', 'name': 'File', 'mimeType': 'text/plain', 'parents': ['folder']},
            'outside': {'id': 'outside', 'parents': []},
        }
        files = _DriveFiles(items)
        client = self._client(files)
        self.assertEqual(client.get_metadata(root='approved', item_ref='file')['item']['id'], 'file')

        items['folder']['parents'] = ['outside']
        with self.assertRaises(GoogleDriveOutsideRootError):
            client.get_metadata(root='approved', item_ref='file')

        ancestry_calls = [call for call in files.get_calls if call['fileId'] in {'folder', 'outside'}]
        self.assertTrue(all(call['fields'] == 'id,parents' for call in ancestry_calls))

    def test_root_is_accepted_but_sibling_cycle_and_depth_are_rejected(self) -> None:
        """Accept the canonical root and reject non-reaching, cyclic, or overlong ancestry."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'sibling': {'id': 'sibling', 'name': 'Sibling', 'mimeType': 'text/plain', 'parents': []},
            'cycle-a': {'id': 'cycle-a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['cycle-b']},
            'cycle-b': {'id': 'cycle-b', 'parents': ['cycle-a']},
        }
        previous = 'root'
        for index in range(101):
            item_id = f'depth-{index}'
            items[item_id] = {'id': item_id, 'parents': [previous]}
            previous = item_id
        items[previous].update(name='Deep', mimeType='text/plain')
        client = self._client(_DriveFiles(items))

        self.assertEqual(client.get_metadata(root='approved', item_ref='root')['item']['id'], 'root')
        for item_ref in ('sibling', 'cycle-a', previous):
            with self.subTest(item_ref=item_ref), self.assertRaises(GoogleDriveOutsideRootError):
                client.get_metadata(root='approved', item_ref=item_ref)

    def test_shortcut_is_returned_without_following_target(self) -> None:
        """Authorize shortcut parentage while never requesting its target ID."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'shortcut': {
                'id': 'shortcut',
                'name': 'Link',
                'mimeType': _SHORTCUT_MIME_TYPE,
                'parents': ['root'],
                'shortcutDetails': {'targetId': 'outside-secret', 'targetMimeType': 'text/plain'},
            },
        }
        files = _DriveFiles(items)
        result = self._client(files).get_metadata(root='approved', item_ref='shortcut')
        self.assertEqual(result['item']['kind'], 'shortcut')
        self.assertNotIn('outside-secret', [call['fileId'] for call in files.get_calls])

    def test_file_root_supports_metadata_but_rejects_list_and_search(self) -> None:
        """Allow an individual configured file only for root listing and metadata lookup."""
        items = {'file-root': {'id': 'file-root', 'name': 'Brief', 'mimeType': 'text/plain'}}
        files = _DriveFiles(items)
        client = self._client(
            files,
            config={'roots': [{'id': 'brief', 'file_id': 'file-root'}]},
        )
        self.assertEqual(client.get_metadata(root='brief', item_ref='file-root')['item']['id'], 'file-root')
        operations: tuple[Callable[[], object], ...] = (
            lambda: client.list_folder(root='brief'),
            lambda: client.search(root='brief', query='brief'),
        )
        for operation in operations:
            with self.assertRaises(GoogleDriveConfigError):
                operation()
        self.assertEqual(files.list_calls, [])

    def test_search_escapes_query_filters_kinds_and_postfilters_candidates(self) -> None:
        """Escape Drive syntax, apply file MIME filters, and discard outside candidates."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'inside': {'id': 'inside', 'parents': ['root']},
            'outside-parent': {'id': 'outside-parent', 'parents': []},
            'outside': {'id': 'outside', 'parents': ['outside-parent']},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {'id': 'outside', 'name': 'Outside', 'mimeType': 'text/plain', 'parents': ['outside-parent']},
                        {
                            'id': 'outside-shortcut',
                            'name': 'Outside link',
                            'mimeType': _SHORTCUT_MIME_TYPE,
                            'parents': ['outside-parent'],
                        },
                        {'id': 'inside', 'name': 'Inside', 'mimeType': 'text/plain', 'parents': ['root']},
                    ]
                }
            ],
        )
        result = self._client(files).search(
            root='approved',
            query=r"a'b\c",
            kinds=('file',),
            max_results=2,
        )
        self.assertEqual([item['id'] for item in result['items']], ['inside'])
        query = files.list_calls[0]['q']
        self.assertIn(r"name contains 'a\'b\\c'", query)
        self.assertIn(f"mimeType != '{_FOLDER_MIME_TYPE}'", query)
        self.assertIn(f"mimeType != '{_SHORTCUT_MIME_TYPE}'", query)
        self.assertNotIn('orderBy', files.list_calls[0])
        outside_parent_calls = [call for call in files.get_calls if call['fileId'] == 'outside-parent']
        self.assertEqual(len(outside_parent_calls), 1)

    def test_search_reuses_parent_metadata_across_sibling_authorization(self) -> None:
        """Share one operation-wide parent cache across all candidate ancestry walks."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'shared-parent': {'id': 'shared-parent', 'parents': ['root']},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['shared-parent']},
                        {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['shared-parent']},
                    ]
                }
            ],
        )

        result = self._client(files).search(root='approved', query='x')

        self.assertEqual([item['id'] for item in result['items']], ['a', 'b'])
        shared_calls = [call for call in files.get_calls if call['fileId'] == 'shared-parent']
        self.assertEqual(len(shared_calls), 1)

    @patch('libs.clients.google_drive.client._MAX_PARENT_FETCHES', 2)
    def test_search_stops_at_total_parent_fetch_budget(self) -> None:
        """Abort malicious unique ancestry chains at the operation-wide provider budget."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'p1': {'id': 'p1', 'parents': ['boundary']},
            'boundary': {'id': 'boundary', 'parents': []},
            'p2': {'id': 'p2', 'parents': []},
        }
        files = _DriveFiles(
            items,
            pages=[
                {
                    'files': [
                        {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['p1']},
                        {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['p2']},
                    ]
                }
            ],
        )

        with self.assertRaisesMessage(
            GoogleDriveAPIError,
            'Google Drive ancestry lookup budget exhausted',
        ):
            self._client(files).search(root='approved', query='x')

        parent_calls = [call for call in files.get_calls if call['fields'] == 'id,parents']
        self.assertEqual([call['fileId'] for call in parent_calls], ['p1', 'boundary'])

    def test_search_rejects_oversized_provider_page_before_ancestry_work(self) -> None:
        """Apply the provider-page bound before authorizing any search candidate."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'parent': {'id': 'parent', 'parents': []},
        }
        candidate = {
            'id': 'outside',
            'name': 'Outside',
            'mimeType': 'text/plain',
            'parents': ['parent'],
        }
        files = _DriveFiles(items, pages=[{'files': [candidate] * 501}])

        with self.assertRaises(GoogleDriveAPIError):
            self._client(files).search(root='approved', query='outside', max_results=100)

        self.assertEqual([call['fileId'] for call in files.get_calls], ['root'])

    def test_search_folder_kind_and_provider_page_bound(self) -> None:
        """Filter folders natively and stop after five provider pages."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        files = _DriveFiles(
            items,
            pages=[{'files': [], 'nextPageToken': f'token-{index}'} for index in range(5)],
        )
        result = self._client(files).search(
            root='approved',
            query='reports',
            kinds=('folder',),
        )
        self.assertEqual(len(files.list_calls), 5)
        self.assertIn(f"mimeType = '{_FOLDER_MIME_TYPE}'", files.list_calls[0]['q'])
        self.assertIsNotNone(result['next_cursor'])

    def test_search_resume_returns_every_ranked_match_exactly_once(self) -> None:
        """Request only remaining capacity so a cursor never skips a page tail."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'outside': {'id': 'outside', 'parents': []},
        }
        ranked = [
            {
                'id': 'outside-1',
                'name': 'Outside 1',
                'mimeType': 'text/plain',
                'parents': ['outside'],
            },
            {'id': 'a', 'name': 'A', 'mimeType': 'text/plain', 'parents': ['root']},
            {
                'id': 'outside-2',
                'name': 'Outside 2',
                'mimeType': 'text/plain',
                'parents': ['outside'],
            },
            {'id': 'b', 'name': 'B', 'mimeType': 'text/plain', 'parents': ['root']},
            {'id': 'c', 'name': 'C', 'mimeType': 'text/plain', 'parents': ['root']},
            {'id': 'd', 'name': 'D', 'mimeType': 'text/plain', 'parents': ['root']},
            {'id': 'e', 'name': 'E', 'mimeType': 'text/plain', 'parents': ['root']},
        ]
        files = _DrivePagedFiles(items, ranked)
        client = self._client(files)

        first = client.search(root='approved', query='report', max_results=3)
        resumed = client.search(
            root='approved',
            query='report',
            cursor=first['next_cursor'],
            max_results=3,
        )

        returned_ids = [item['id'] for item in first['items'] + resumed['items']]
        self.assertEqual(returned_ids, ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(len(returned_ids), len(set(returned_ids)))
        self.assertEqual([call['pageSize'] for call in files.list_calls], [3, 2, 3])

    def test_search_combined_file_and_folder_kinds_excludes_shortcuts(self) -> None:
        """Exclude shortcuts when callers explicitly request files and folders."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        files = _DriveFiles(items, pages=[{'files': []}])
        self._client(files).search(
            root='approved',
            query='reports',
            kinds=('folder', 'file'),
        )
        self.assertIn(f"mimeType != '{_SHORTCUT_MIME_TYPE}'", files.list_calls[0]['q'])

    def test_invalid_kind_fails_before_service_construction(self) -> None:
        """Reject unsupported kinds without resolving credentials or creating a service."""
        builds: list[bool] = []
        client = self._client(_DriveFiles({}), builds=builds)
        with self.assertRaises(GoogleDriveConfigError):
            client.search(root='approved', query='x', kinds=('shortcut',))
        self.assertEqual(builds, [])

    def test_cursor_mismatches_and_malformed_values_fail_before_api(self) -> None:
        """Bind opaque cursors to instance, root, locator, operation, query, and kinds."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        first_files = _DriveFiles(
            items,
            pages=[{'files': [], 'nextPageToken': f'next-{index}'} for index in range(5)],
        )
        cursor = self._client(first_files).search(
            root='approved',
            query='reports',
            kinds=('file',),
        )['next_cursor']
        self.assertIsInstance(cursor, str)
        self.assertNotIn('next', cursor)

        cases = (
            self._client(_DriveFiles(items), instance_id='other'),
            self._client(
                _DriveFiles({'other-root': items['root']}),
                config={'roots': [{'id': 'approved', 'file_id': 'other-root'}]},
            ),
        )
        for client in cases:
            with self.assertRaises(GoogleDriveInvalidCursorError):
                client.search(root='approved', query='reports', kinds=('file',), cursor=cursor)
        for mismatch_query, mismatch_kinds in (
            ('different', ('file',)),
            ('reports', ('folder',)),
        ):
            files = _DriveFiles(items)
            with self.assertRaises(GoogleDriveInvalidCursorError):
                self._client(files).search(
                    root='approved',
                    query=mismatch_query,
                    kinds=mismatch_kinds,
                    cursor=cursor,
                )
            self.assertEqual(files.get_calls, [])
            self.assertEqual(files.list_calls, [])
        for malformed in ('', 'not-base64', 'e30='):
            files = _DriveFiles(items)
            with self.assertRaises(GoogleDriveInvalidCursorError):
                self._client(files).search(root='approved', query='reports', kinds=('file',), cursor=malformed)
            self.assertEqual(files.get_calls, [])

        padding = '=' * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        payload['v'] = True
        bool_version = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        files = _DriveFiles(items)
        with self.assertRaises(GoogleDriveInvalidCursorError):
            self._client(files).search(
                root='approved',
                query='reports',
                kinds=('file',),
                cursor=bool_version,
            )
        self.assertEqual(files.get_calls, [])

    def test_cursor_resource_limits_and_deep_json_fail_before_api(self) -> None:
        """Bound cursor decoding and suppress parser recursion context before services."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        payload = {
            'v': 1,
            'instance': 'drive',
            'root': 'approved',
            'root_locator': 'root',
            'operation': 'search',
            'query': 'reports',
            'kinds': ['file'],
            'provider_cursor': 'next',
            'resource_ref': None,
            'resource_default': False,
        }
        malformed_payloads: tuple[Any, ...] = (
            'x' * 70_000,
            {**payload, 'provider_cursor': 'x' * 16_385},
            {**payload, 'instance': 'x' * 4_097},
            {**payload, 'kinds': ['file', 'folder', 'file']},
        )
        cursors = ['A' * 131_073]
        for malformed in malformed_payloads:
            encoded = base64.urlsafe_b64encode(json.dumps(malformed).encode()).rstrip(b'=').decode()
            cursors.append(encoded)
        deep_cursor = base64.urlsafe_b64encode(('[' * 2_000 + '0' + ']' * 2_000).encode()).rstrip(b'=').decode()
        cursors.append(deep_cursor)

        for cursor in cursors:
            files = _DriveFiles(items)
            with self.subTest(length=len(cursor)), self.assertRaises(GoogleDriveInvalidCursorError) as caught:
                self._client(files).search(root='approved', query='reports', kinds=('file',), cursor=cursor)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(files.get_calls, [])
            self.assertEqual(files.list_calls, [])

    def test_cursor_encoder_enforces_search_provider_token_limit(self) -> None:
        """Emit the decoder boundary token and reject one-byte provider overrun."""
        client = self._client(_DriveFiles({}))
        root = client._config.roots[0]

        cursor = client._encode_cursor(
            root=root,
            operation='search',
            query='x',
            kinds=(),
            provider_cursor='p' * 16_384,
        )
        self.assertEqual(
            client._decode_cursor(
                cursor,
                root=root,
                operation='search',
                query='x',
                kinds=(),
            ),
            'p' * 16_384,
        )
        with self.assertRaises(GoogleDriveAPIError):
            client._encode_cursor(
                root=root,
                operation='search',
                query='x',
                kinds=(),
                provider_cursor='p' * 16_385,
            )

    def test_search_maps_oversized_provider_token_to_safe_typed_failure(self) -> None:
        """Never emit an unresumable cursor or expose its provider token."""
        items = {'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE}}
        provider_token = 'PRIVATE-' + 'p' * 16_385
        files = _DriveFiles(
            items,
            pages=[{'files': [], 'nextPageToken': provider_token} for _index in range(5)],
        )

        with self.assertRaises(GoogleDriveAPIError) as caught:
            self._client(files).search(root='approved', query='x')

        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_list_cursor_operation_mismatch_and_resume_reauthorizes(self) -> None:
        """Reject cross-operation cursors and recheck every resumed candidate's ancestry."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'outside': {'id': 'outside', 'parents': []},
        }
        first_files = _DriveFiles(
            items,
            pages=[{'files': [], 'nextPageToken': f'next-{index}'} for index in range(5)],
        )
        cursor = self._client(first_files).search(root='approved', query='x')['next_cursor']
        resumed_files = _DriveFiles(
            items,
            pages=[{'files': [{'id': 'moved', 'name': 'Moved', 'mimeType': 'text/plain', 'parents': ['outside']}]}],
        )
        with self.assertRaises(GoogleDriveInvalidCursorError):
            self._client(resumed_files).list_folder(root='approved', cursor=cursor)
        result = self._client(resumed_files).search(root='approved', query='x', cursor=cursor)
        self.assertEqual(result['items'], [])
        self.assertIn('outside', [call['fileId'] for call in resumed_files.get_calls])

    def test_provider_failure_traceback_releases_service_and_request(self) -> None:
        """Clear credential-bearing provider objects after operation-local service creation."""
        root_request = MagicMock()
        root_request.execute.return_value = {
            'id': 'root',
            'name': 'Root',
            'mimeType': _FOLDER_MIME_TYPE,
        }
        list_request = MagicMock()
        list_request.execute.side_effect = _http_failure(403)
        files_resource = MagicMock()
        files_resource.get.return_value = root_request
        files_resource.list.return_value = list_request
        service = MagicMock()
        service.files.return_value = files_resource
        service.credential_marker = 'PRIVATE-CREDENTIAL-OBJECT'
        client = GoogleDriveClient(
            token_supplier=lambda: '{}',
            config={'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id='drive',
            service_factory=lambda _raw, _subject: service,
        )

        try:
            client.list_folder(root='approved')
        except GoogleDriveForbiddenError as failure:
            frames = _client_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is service for value in retained_values))
            self.assertFalse(any(value is list_request for value in retained_values))
            self.assertTrue(all('context' not in values for _name, values in frames))
            for _name, values in frames:
                if 'request' in values:
                    self.assertIsNone(values['request'])
        else:
            self.fail('provider failure did not raise GoogleDriveForbiddenError')

    def test_search_failure_traceback_clears_provider_response_locals(self) -> None:
        """Clear search responses and requests before a typed provider failure propagates."""
        root_request = MagicMock()
        root_request.execute.return_value = {
            'id': 'root',
            'name': 'Root',
            'mimeType': _FOLDER_MIME_TYPE,
        }
        parent_request = MagicMock()
        parent_request.execute.side_effect = _http_failure(403)
        page = {
            'files': [
                {
                    'id': 'candidate',
                    'name': 'Candidate',
                    'mimeType': 'text/plain',
                    'parents': ['parent'],
                }
            ],
            'credential_marker': 'PRIVATE-RESPONSE-OBJECT',
        }
        list_request = MagicMock()
        list_request.execute.return_value = page
        files_resource = MagicMock()
        files_resource.get.side_effect = lambda **kwargs: (
            root_request if kwargs['fileId'] == 'root' else parent_request
        )
        files_resource.list.return_value = list_request
        service = MagicMock()
        service.files.return_value = files_resource
        client = GoogleDriveClient(
            token_supplier=lambda: '{}',
            config={'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id='drive',
            service_factory=lambda _raw, _subject: service,
        )

        try:
            client.search(root='approved', query='candidate')
        except GoogleDriveForbiddenError as failure:
            frames = _client_traceback_locals(failure)
            self.assertTrue(all('context' not in values for _name, values in frames))
            for _name, values in frames:
                for local_name in ('request', 'root_item', 'page', 'raw_items', 'raw', 'parent'):
                    if local_name in values:
                        self.assertIsNone(values[local_name])
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is service for value in retained_values))
            self.assertFalse(any(value is parent_request for value in retained_values))
            self.assertFalse(any(value is page for value in retained_values))
        else:
            self.fail('search provider failure did not raise GoogleDriveForbiddenError')

    def test_list_malformed_metadata_traceback_releases_provider_objects(self) -> None:
        """Map malformed list metadata without retaining its response, request, or service."""
        malformed = {
            'id': 'candidate',
            'name': 'Candidate',
            'mimeType': 'text/plain',
            'parents': ['root'],
            'size': 'not-an-integer',
            'credential_marker': 'PRIVATE-LIST-RESPONSE',
        }
        root_request = MagicMock()
        root_request.execute.return_value = {
            'id': 'root',
            'name': 'Root',
            'mimeType': _FOLDER_MIME_TYPE,
        }
        list_request = MagicMock()
        list_request.execute.return_value = {'files': [malformed]}
        files_resource = MagicMock()
        files_resource.get.return_value = root_request
        files_resource.list.return_value = list_request
        service = MagicMock()
        service.files.return_value = files_resource
        client = GoogleDriveClient(
            token_supplier=lambda: '{}',
            config={'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id='drive',
            service_factory=lambda _raw, _subject: service,
        )

        try:
            client.list_folder(root='approved')
        except GoogleDriveAPIError as failure:
            frames = _client_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            for sentinel in (malformed, list_request, service):
                self.assertFalse(any(value is sentinel for value in retained_values))
            self.assertNotIn('PRIVATE-LIST-RESPONSE', repr(frames))
            self.assertEqual(str(failure), 'Google Drive returned invalid metadata')
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('malformed list metadata did not raise GoogleDriveAPIError')

    def test_search_malformed_metadata_traceback_releases_provider_objects(self) -> None:
        """Map malformed search metadata without retaining its response, request, or service."""
        malformed = {
            'id': 'candidate',
            'name': 'Candidate',
            'mimeType': 'text/plain',
            'parents': ['root'],
            'size': 'not-an-integer',
            'credential_marker': 'PRIVATE-SEARCH-RESPONSE',
        }
        root_request = MagicMock()
        root_request.execute.return_value = {
            'id': 'root',
            'name': 'Root',
            'mimeType': _FOLDER_MIME_TYPE,
        }
        search_request = MagicMock()
        search_request.execute.return_value = {'files': [malformed]}
        files_resource = MagicMock()
        files_resource.get.return_value = root_request
        files_resource.list.return_value = search_request
        service = MagicMock()
        service.files.return_value = files_resource
        client = GoogleDriveClient(
            token_supplier=lambda: '{}',
            config={'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id='drive',
            service_factory=lambda _raw, _subject: service,
        )

        try:
            client.search(root='approved', query='candidate')
        except GoogleDriveAPIError as failure:
            frames = _client_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            for sentinel in (malformed, search_request, service):
                self.assertFalse(any(value is sentinel for value in retained_values))
            self.assertNotIn('PRIVATE-SEARCH-RESPONSE', repr(frames))
            self.assertEqual(str(failure), 'Google Drive returned invalid metadata')
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('malformed search metadata did not raise GoogleDriveAPIError')

    def test_authorization_failure_traceback_releases_service(self) -> None:
        """Clear the built service when a current ancestry walk rejects an item."""
        items: dict[str, dict[str, Any]] = {
            'root': {'id': 'root', 'name': 'Root', 'mimeType': _FOLDER_MIME_TYPE},
            'outside': {'id': 'outside', 'name': 'Outside', 'mimeType': 'text/plain', 'parents': []},
        }
        files = _DriveFiles(items)
        service = _DriveService(files)
        client = GoogleDriveClient(
            token_supplier=lambda: '{}',
            config={'roots': [{'id': 'approved', 'file_id': 'root'}]},
            instance_id='drive',
            service_factory=lambda _raw, _subject: service,
        )

        try:
            client.get_metadata(root='approved', item_ref='outside')
        except GoogleDriveOutsideRootError as failure:
            frames = _client_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is service for value in retained_values))
            self.assertTrue(all('context' not in values for _name, values in frames))
        else:
            self.fail('authorization failure did not raise GoogleDriveOutsideRootError')
