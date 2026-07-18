# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django-free Google Drive v3 metadata client foundation.

Credentials are supplied and parsed only while constructing an operation-local service.
The client retains only suppliers, injectable factories, and validated non-secret config.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from googleapiclient.errors import HttpError
from libs.clients.google_drive.config import (
    GoogleDriveConfig,
    GoogleDriveRoot,
    parse_google_drive_config,
)
from libs.clients.google_drive.errors import (
    GoogleDriveAPIError,
    GoogleDriveAuthError,
    GoogleDriveConfigError,
    GoogleDriveError,
    GoogleDriveForbiddenError,
    GoogleDriveInvalidCursorError,
    GoogleDriveNotFoundError,
    GoogleDriveOutsideRootError,
    GoogleDriveRateLimitedError,
)

DRIVE_METADATA_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly'
DRIVE_FIELDS = 'id,name,mimeType,size,modifiedTime,parents,webViewLink,driveId,shortcutDetails'

ServiceFactory = Callable[[str, str | None], Any]


@dataclass(slots=True)
class _OperationContext:
    """Hold operation-local provider state, ancestry cache, and lookup accounting."""

    service: Any
    parent_cache: dict[str, tuple[str, ...]] = field(default_factory=dict)
    parent_fetches: int = 0

    def clear(self) -> None:
        """Release provider objects and responses before a failure traceback escapes."""
        self.service = None
        self.parent_cache.clear()


_FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
_SHORTCUT_MIME_TYPE = 'application/vnd.google-apps.shortcut'
_MAX_RESULTS = 100
_MAX_PROVIDER_PAGES = 5
_MAX_ENCODED_CURSOR_LENGTH = 131_072
_MAX_CURSOR_PAYLOAD_BYTES = 65_536
_MAX_PROVIDER_STATE_LENGTH = 65_536
_MAX_PROVIDER_CURSOR_LENGTH = 16_384
_MAX_CURSOR_FIELD_LENGTH = 4_096
# Opaque Drive IDs are normally far shorter; 4 KiB preserves provider compatibility
# while preventing caller-controlled request and cursor amplification.
_MAX_ITEM_REF_LENGTH = 4_096
_MAX_ROOT_ALIAS_LENGTH = 256
# Drive does not publish a useful query-text ceiling, so retain a practical 4 KiB.
_MAX_QUERY_LENGTH = 4_096
_MAX_PENDING_REFS = 500
_MAX_PROVIDER_PAGE_ENTRIES = 500
_MAX_ANCESTRY_DEPTH = 100
# Bound total parent lookups as well as per-item depth to prevent request amplification.
_MAX_PARENT_FETCHES = 200
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
# A single provider-directed retry must never suspend an agent operation indefinitely.
_MAX_RETRY_AFTER_SECONDS = 60.0
_QUOTA_REASONS = frozenset(
    {
        'dailyLimitExceeded',
        'downloadQuotaExceeded',
        'quotaExceeded',
        'rateLimitExceeded',
        'sharingRateLimitExceeded',
        'storageQuotaExceeded',
        'userRateLimitExceeded',
    }
)


def _build_service(raw_credential: str, subject: str | None) -> Any:
    """Build one Drive v3 service from a complete service-account JSON value."""
    from google.oauth2 import service_account  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    info: Any = None
    credentials: Any = None
    try:
        invalid_json = False
        try:
            info = json.loads(raw_credential)
        except (TypeError, ValueError):
            invalid_json = True
        if invalid_json:
            # Raise after the parser's except block so no JSONDecodeError is retained.
            raise GoogleDriveAuthError('Google service-account credential is not valid JSON') from None
        if not isinstance(info, dict):
            raise GoogleDriveAuthError('Google service-account credential must be a JSON object')
        build_failed = False
        try:
            credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                info,
                scopes=(DRIVE_METADATA_SCOPE,),
            )
            if subject:
                credentials = credentials.with_subject(subject)
            return build(
                'drive',
                'v3',
                credentials=credentials,
                cache_discovery=False,
            )
        except GoogleDriveError:
            raise
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            build_failed = True
        # Do not retain vendor failures that may echo credential fields.
        if build_failed:
            raise GoogleDriveAuthError('failed to build Google Drive credentials') from None
        raise GoogleDriveAuthError('failed to build Google Drive credentials') from None
    finally:
        # Tracebacks retain frame locals, so overwrite every credential-bearing value.
        raw_credential = ''
        info = None
        credentials = None


def _status(exc: Exception) -> int | None:
    """Extract an HTTP status without formatting provider response content."""
    if not isinstance(exc, HttpError) or exc.resp is None:
        return None
    status = getattr(exc.resp, 'status', None)
    return status if isinstance(status, int) else None


def _retry_after(exc: Exception) -> float:
    """Return a finite positive Retry-After delay capped at sixty seconds."""
    if not isinstance(exc, HttpError) or exc.resp is None:
        return 0.0
    value = exc.resp.get('retry-after')
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(delay) or delay <= 0:
        return 0.0
    return min(delay, _MAX_RETRY_AFTER_SECONDS)


def _is_quota_failure(exc: Exception) -> bool:
    """Recognize Google quota reasons while keeping response content out of messages."""
    if not isinstance(exc, HttpError):
        return False
    try:
        payload = json.loads(exc.content.decode('utf-8'))
        entries = payload.get('error', {}).get('errors', [])
    except (AttributeError, UnicodeError, ValueError):
        return False
    if not isinstance(entries, list):
        return False
    return any(isinstance(entry, Mapping) and entry.get('reason') in _QUOTA_REASONS for entry in entries)


def _map_failure(exc: Exception, *, operation: str) -> GoogleDriveError:
    """Translate one provider failure into a typed failure with a safe message."""
    status = _status(exc)
    context = f'Google Drive {operation} failed'
    if status == 401:
        return GoogleDriveAuthError(f'{context} (status 401)')
    if status == 429 or (status == 403 and _is_quota_failure(exc)):
        return GoogleDriveRateLimitedError(f'{context} (rate limited)')
    if status == 403:
        return GoogleDriveForbiddenError(f'{context} (status 403)')
    if status == 404:
        return GoogleDriveNotFoundError(f'{context} (status 404)')
    return GoogleDriveAPIError(context, status=status)


def _escape_drive_query(value: str) -> str:
    """Escape backslashes and apostrophes for one Drive query string literal."""
    return value.replace('\\', '\\\\').replace("'", "\\'")


class GoogleDriveClient:
    """Build operation-local Drive services and normalize metadata-only responses."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
        service_factory: ServiceFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """Create a metadata client without resolving or retaining plaintext credentials."""
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise GoogleDriveConfigError('instance_id must be a non-empty string')
        self._token_supplier = token_supplier
        self._config: GoogleDriveConfig = parse_google_drive_config(config or {})
        self._instance_id = instance_id.strip()
        self._service_factory = service_factory or _build_service
        self._sleep_fn = sleep_fn or time.sleep

    def _service(self) -> Any:
        """Resolve the Google credential and build one service for the current operation."""
        raw_credential: str | None = None
        try:
            raw_credential = self._token_supplier()
            if not raw_credential:
                raise GoogleDriveAuthError('no Google service-account credential resolved')
            factory_failed = False
            try:
                return self._service_factory(raw_credential, self._config.subject)
            except GoogleDriveError:
                raise
            except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                factory_failed = True
            # Raise outside the except block to avoid retaining a secret-bearing vendor failure.
            if factory_failed:
                raise GoogleDriveAuthError('failed to build Google Drive credentials') from None
            raise GoogleDriveAuthError('failed to build Google Drive credentials') from None
        finally:
            # The caller frame is part of the propagated traceback on auth failures.
            raw_credential = None

    def _normalize_item(
        self,
        raw: Mapping[str, Any],
        *,
        root_alias: str,
    ) -> dict[str, Any]:
        """Return the metadata-only cross-provider shape for one Drive item."""
        raw_size: Any = None
        parents: Any = None
        shortcut_details: Any = None
        result: dict[str, Any] | None = None
        normalization_failed = False
        try:
            mime_type = raw.get('mimeType')
            if mime_type == _FOLDER_MIME_TYPE:
                kind = 'folder'
            elif mime_type == _SHORTCUT_MIME_TYPE:
                kind = 'shortcut'
            else:
                kind = 'file'

            raw_size = raw.get('size')
            size = int(raw_size) if raw_size is not None else None
            parents = raw.get('parents')
            parent_refs = [parent for parent in parents if isinstance(parent, str)] if isinstance(parents, list) else []
            provider_metadata: dict[str, Any] = {}
            drive_id = raw.get('driveId')
            if isinstance(drive_id, str) and drive_id:
                provider_metadata['drive_id'] = drive_id
            shortcut_details = raw.get('shortcutDetails')
            if isinstance(shortcut_details, Mapping):
                target_mime_type = shortcut_details.get('targetMimeType')
                if isinstance(target_mime_type, str) and target_mime_type:
                    provider_metadata['shortcut_target_mime_type'] = target_mime_type

            result = {
                'provider': 'google_drive',
                'root': root_alias,
                'id': raw.get('id'),
                'name': raw.get('name'),
                'kind': kind,
                'mime_type': mime_type,
                'size': size,
                'modified_at': raw.get('modifiedTime'),
                'parent_refs': parent_refs,
                'path': None,
                'web_url': raw.get('webViewLink') or None,
                'provider_metadata': provider_metadata,
            }
        except (TypeError, ValueError, OverflowError):
            normalization_failed = True
        finally:
            # Provider responses may contain fields outside DRIVE_FIELDS; never retain
            # the complete response or malformed values in a propagated traceback.
            del raw
            raw_size = None
            parents = None
            shortcut_details = None
        if normalization_failed:
            raise GoogleDriveAPIError('Google Drive returned invalid metadata') from None
        if result is None:  # pragma: no cover - all uncaught paths either assign or raise
            raise GoogleDriveAPIError('Google Drive returned invalid metadata')
        return result

    def _validate_max_results(self, max_results: int) -> int:
        """Require an integer provider page size from one through one hundred."""
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= _MAX_RESULTS:
            raise GoogleDriveConfigError('max_results must be an integer from 1 through 100')
        return max_results

    def _root(self, alias: str) -> GoogleDriveRoot:
        """Select one configured root alias without making a provider request."""
        if not isinstance(alias, str) or not alias or len(alias) > _MAX_ROOT_ALIAS_LENGTH:
            raise GoogleDriveConfigError('root must identify a configured alias')
        for configured_root in self._config.roots:
            if configured_root.id == alias:
                return configured_root
        raise GoogleDriveConfigError(f'unknown Google Drive root: {alias}')

    def _resolve_root(
        self,
        context: _OperationContext,
        *,
        root: GoogleDriveRoot,
    ) -> dict[str, Any]:
        """Resolve a configured locator to current metadata and its canonical ID."""
        raw: Any = None
        try:
            raw = self._execute(
                context.service.files().get(
                    fileId=root.file_id,
                    fields=DRIVE_FIELDS,
                    supportsAllDrives=True,
                ),
                operation='resolve root',
            )
            if not isinstance(raw, Mapping) or not isinstance(raw.get('id'), str):
                raise GoogleDriveAPIError('Google Drive resolve root returned invalid metadata')
            return dict(raw)
        finally:
            raw = None
            del context

    def _assert_within_root(
        self,
        context: _OperationContext,
        *,
        item: Mapping[str, Any],
        canonical_root_id: str,
    ) -> None:
        """Walk current parents until the canonical root is reached or reject the item."""
        parent: Any = None
        try:
            item_id = item.get('id')
            if item_id == canonical_root_id:
                return
            if not isinstance(item_id, str):
                raise GoogleDriveOutsideRootError('Google Drive item is outside the configured root')
            raw_parents = item.get('parents')
            parents = (
                [parent_id for parent_id in raw_parents if isinstance(parent_id, str)]
                if isinstance(raw_parents, list)
                else []
            )
            pending = [(parent_id, 1) for parent_id in parents]
            visited = {item_id}
            while pending:
                parent_id, depth = pending.pop()
                if depth > _MAX_ANCESTRY_DEPTH or parent_id in visited:
                    continue
                if parent_id == canonical_root_id:
                    return
                visited.add(parent_id)
                cached_parents = context.parent_cache.get(parent_id)
                if cached_parents is None:
                    if context.parent_fetches >= _MAX_PARENT_FETCHES:
                        raise GoogleDriveAPIError('Google Drive ancestry lookup budget exhausted')
                    context.parent_fetches += 1
                    parent = self._execute(
                        context.service.files().get(
                            fileId=parent_id,
                            fields='id,parents',
                            supportsAllDrives=True,
                        ),
                        operation='authorize item',
                    )
                    parent_parents = parent.get('parents') if isinstance(parent, Mapping) else None
                    cached_parents = (
                        tuple(next_parent for next_parent in parent_parents if isinstance(next_parent, str))
                        if isinstance(parent_parents, list)
                        else ()
                    )
                    context.parent_cache[parent_id] = cached_parents
                    parent = None
                pending.extend((next_parent, depth + 1) for next_parent in cached_parents)
            raise GoogleDriveOutsideRootError('Google Drive item is outside the configured root')
        finally:
            parent = None
            item = {}
            del context

    def _validate_kinds(self, kinds: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize supported search kinds into a stable cursor-binding order."""
        if not isinstance(kinds, tuple) or len(kinds) > 2 or any(kind not in {'file', 'folder'} for kind in kinds):
            raise GoogleDriveConfigError('kinds must contain only file or folder')
        return tuple(sorted(set(kinds)))

    def _encode_cursor(
        self,
        *,
        root: GoogleDriveRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        provider_cursor: str | None,
        resource_ref: str | None = None,
        resource_default: bool = False,
    ) -> str | None:
        """Wrap a provider page token in an opaque invocation-bound envelope."""
        if not provider_cursor:
            return None
        binding_values = (self._instance_id, root.id, root.file_id, operation)
        if (
            len(provider_cursor) > _MAX_PROVIDER_STATE_LENGTH
            or (operation == 'search' and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
            or any(len(value) > _MAX_CURSOR_FIELD_LENGTH for value in binding_values)
            or (query is not None and len(query) > _MAX_CURSOR_FIELD_LENGTH)
            or (resource_ref is not None and len(resource_ref) > _MAX_CURSOR_FIELD_LENGTH)
        ):
            raise GoogleDriveAPIError('Google Drive cursor state exceeds the safe size limit')
        payload = {
            'v': 1,
            'instance': self._instance_id,
            'root': root.id,
            'root_locator': root.file_id,
            'operation': operation,
            'query': query,
            'kinds': list(kinds),
            'provider_cursor': provider_cursor,
            'resource_ref': resource_ref,
            'resource_default': resource_default,
        }
        raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
        if len(raw) > _MAX_CURSOR_PAYLOAD_BYTES:
            raise GoogleDriveAPIError('Google Drive cursor payload exceeds the safe size limit')
        encoded = base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')
        if len(encoded) > _MAX_ENCODED_CURSOR_LENGTH:
            raise GoogleDriveAPIError('Google Drive cursor exceeds the safe size limit')
        return encoded

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        root: GoogleDriveRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        resource_ref: str | None = None,
        resource_default: bool = False,
        unresolved_default: bool = False,
    ) -> str | None:
        """Strictly validate an opaque cursor before any provider interaction."""
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_ENCODED_CURSOR_LENGTH:
            raise GoogleDriveInvalidCursorError('Google Drive cursor is invalid')
        invalid_cursor = False
        try:
            padding = '=' * (-len(cursor) % 4)
            raw = base64.b64decode(
                cursor + padding,
                altchars=b'-_',
                validate=True,
            )
            if len(raw) > _MAX_CURSOR_PAYLOAD_BYTES:
                raise ValueError
            payload = json.loads(raw.decode('utf-8'))
        except (binascii.Error, RecursionError, UnicodeError, ValueError):
            invalid_cursor = True
            payload = None
        if invalid_cursor:
            raise GoogleDriveInvalidCursorError('Google Drive cursor is invalid') from None
        expected_fields = {
            'v',
            'instance',
            'root',
            'root_locator',
            'operation',
            'query',
            'kinds',
            'provider_cursor',
            'resource_ref',
            'resource_default',
        }
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise GoogleDriveInvalidCursorError('Google Drive cursor is invalid')
        cursor_kinds = payload.get('kinds')
        string_fields = ('instance', 'root', 'root_locator', 'operation')
        valid_strings = all(
            isinstance(payload.get(field), str)
            and bool(payload.get(field))
            and len(payload[field]) <= _MAX_CURSOR_FIELD_LENGTH
            for field in string_fields
        )
        valid_query = payload.get('query') is None or (
            isinstance(payload.get('query'), str) and len(payload['query']) <= _MAX_CURSOR_FIELD_LENGTH
        )
        valid_kinds = (
            isinstance(cursor_kinds, list)
            and len(cursor_kinds) <= 2
            and all(isinstance(kind, str) and len(kind) <= _MAX_CURSOR_FIELD_LENGTH for kind in cursor_kinds)
        )
        cursor_resource = payload.get('resource_ref')
        valid_resource = cursor_resource is None or (
            isinstance(cursor_resource, str)
            and bool(cursor_resource)
            and len(cursor_resource) <= _MAX_CURSOR_FIELD_LENGTH
        )
        valid_resource_mode = isinstance(payload.get('resource_default'), bool)
        version = payload.get('v')
        valid_version = isinstance(version, int) and not isinstance(version, bool) and version == 1
        if (
            not valid_version
            or not valid_strings
            or not valid_query
            or not valid_kinds
            or not valid_resource
            or not valid_resource_mode
            or (payload.get('resource_default') is True and not isinstance(cursor_resource, str))
        ):
            raise GoogleDriveInvalidCursorError('Google Drive cursor is invalid')
        provider_cursor = payload['provider_cursor']
        if (
            not isinstance(provider_cursor, str)
            or not provider_cursor
            or len(provider_cursor) > _MAX_PROVIDER_STATE_LENGTH
            or (operation == 'search' and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
        ):
            raise GoogleDriveInvalidCursorError('Google Drive cursor is invalid')
        expected = (
            self._instance_id,
            root.id,
            root.file_id,
            operation,
            query,
            list(kinds),
            resource_default,
        )
        actual = (
            payload['instance'],
            payload['root'],
            payload['root_locator'],
            payload['operation'],
            payload['query'],
            cursor_kinds,
            payload['resource_default'],
        )
        if actual != expected:
            raise GoogleDriveInvalidCursorError('Google Drive cursor does not match this operation')
        if not unresolved_default and cursor_resource != resource_ref:
            raise GoogleDriveInvalidCursorError('Google Drive cursor does not match this resource')
        if unresolved_default and not resource_default:
            raise GoogleDriveInvalidCursorError('Google Drive cursor does not match this resource')
        return provider_cursor

    def _provider_state(self, value: str | None) -> tuple[str | None, list[str]]:
        """Decode bounded list pagination state into provider and buffered references."""
        if value is None:
            return None, []
        try:
            state = json.loads(value)
        except (RecursionError, ValueError):
            raise GoogleDriveInvalidCursorError('Google Drive cursor state is invalid') from None
        if not isinstance(state, dict) or set(state) != {'cursor', 'pending'}:
            raise GoogleDriveInvalidCursorError('Google Drive cursor state is invalid')
        provider_cursor = state.get('cursor')
        pending = state.get('pending')
        if (
            (provider_cursor is not None and (not isinstance(provider_cursor, str) or not provider_cursor))
            or (isinstance(provider_cursor, str) and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
            or not isinstance(pending, list)
            or len(pending) > _MAX_PENDING_REFS
            or any(not isinstance(item_ref, str) or not item_ref for item_ref in pending)
            or any(len(item_ref) > _MAX_ITEM_REF_LENGTH for item_ref in pending if isinstance(item_ref, str))
        ):
            raise GoogleDriveInvalidCursorError('Google Drive cursor state is invalid')
        return provider_cursor, list(pending)

    def _provider_state_value(self, provider_cursor: str | None, pending: list[str]) -> str | None:
        """Encode provider continuation and metadata-only buffered item IDs."""
        if provider_cursor is None and not pending:
            return None
        if (
            (provider_cursor is not None and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
            or len(pending) > _MAX_PENDING_REFS
            or any(not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH for item_ref in pending)
        ):
            raise GoogleDriveAPIError('Google Drive pagination state exceeds the safe buffering limit')
        value = json.dumps({'cursor': provider_cursor, 'pending': pending}, separators=(',', ':'), sort_keys=True)
        if len(value) > _MAX_PROVIDER_STATE_LENGTH:
            raise GoogleDriveAPIError('Google Drive pagination state exceeds the safe size limit')
        return value

    def _assert_direct_child(self, item: Mapping[str, Any], *, folder_ref: str) -> None:
        """Require provider metadata to retain the exact selected direct parent."""
        parents = item.get('parents')
        if not isinstance(parents, list) or folder_ref not in parents:
            raise GoogleDriveOutsideRootError('Google Drive item is no longer a direct child of the selected folder')

    def _list_kwargs(self, root: GoogleDriveRoot) -> dict[str, Any]:
        """Return location flags shared by Drive listing and search requests."""
        kwargs: dict[str, Any] = {
            'corpora': root.corpus,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True,
        }
        if root.corpus == 'drive':
            kwargs['driveId'] = root.drive_id
        return kwargs

    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured roots only."""
        context = _OperationContext(service=self._service())
        try:
            items = [
                self._normalize_item(
                    self._resolve_root(context, root=root),
                    root_alias=root.id,
                )
                for root in self._config.roots
            ]
            return {'items': items, 'next_cursor': None}
        finally:
            context.clear()
            del context

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one page of direct children beneath an authorized folder."""
        page_size = self._validate_max_results(max_results)
        configured_root = self._root(root)
        if folder_ref is not None and (
            not isinstance(folder_ref, str) or not folder_ref or len(folder_ref) > _MAX_ITEM_REF_LENGTH
        ):
            raise GoogleDriveConfigError('folder_ref must be a non-empty string')
        folder_default = folder_ref is None
        encoded_state = self._decode_cursor(
            cursor,
            root=configured_root,
            operation='list_folder',
            query=None,
            kinds=(),
            resource_ref=folder_ref,
            resource_default=folder_default,
            unresolved_default=folder_default,
        )
        provider_cursor, pending_refs = self._provider_state(encoded_state)
        context = _OperationContext(service=self._service())
        root_item: Any = None
        selected_folder: Any = None
        page: Any = None
        raw_items: Any = None
        raw: Any = None
        try:
            root_item = self._resolve_root(context, root=configured_root)
            canonical_root_id = root_item['id']
            selected_folder = root_item
            selected_ref = canonical_root_id
            if folder_default:
                encoded_state = self._decode_cursor(
                    cursor,
                    root=configured_root,
                    operation='list_folder',
                    query=None,
                    kinds=(),
                    resource_ref=selected_ref,
                    resource_default=True,
                )
                provider_cursor, pending_refs = self._provider_state(encoded_state)
            else:
                selected_ref = folder_ref
                selected_folder = self._execute(
                    context.service.files().get(
                        fileId=folder_ref,
                        fields=DRIVE_FIELDS,
                        supportsAllDrives=True,
                    ),
                    operation='get folder metadata',
                )
            self._assert_within_root(
                context,
                item=selected_folder,
                canonical_root_id=canonical_root_id,
            )
            if selected_folder.get('mimeType') != _FOLDER_MIME_TYPE:
                raise GoogleDriveConfigError('selected Google Drive root or item is not a folder')
            items: list[dict[str, Any]] = []
            while pending_refs and len(items) < page_size:
                item_ref = pending_refs.pop(0)
                raw = self._execute(
                    context.service.files().get(
                        fileId=item_ref,
                        fields=DRIVE_FIELDS,
                        supportsAllDrives=True,
                    ),
                    operation='resume folder listing',
                )
                self._assert_within_root(context, item=raw, canonical_root_id=canonical_root_id)
                self._assert_direct_child(raw, folder_ref=selected_ref)
                items.append(self._normalize_item(raw, root_alias=configured_root.id))
                raw = None
            if len(items) >= page_size:
                return {
                    'items': items,
                    'next_cursor': self._encode_cursor(
                        root=configured_root,
                        operation='list_folder',
                        query=None,
                        kinds=(),
                        provider_cursor=self._provider_state_value(provider_cursor, pending_refs),
                        resource_ref=selected_ref,
                        resource_default=folder_default,
                    ),
                }
            if cursor is not None and provider_cursor is None:
                return {'items': items, 'next_cursor': None}
            kwargs = {
                **self._list_kwargs(configured_root),
                'q': f"'{_escape_drive_query(selected_ref)}' in parents and trashed = false",
                'fields': f'nextPageToken,files({DRIVE_FIELDS})',
                'pageSize': page_size,
                'orderBy': 'folder,name_natural',
            }
            if provider_cursor is not None:
                kwargs['pageToken'] = provider_cursor
            page = self._execute(context.service.files().list(**kwargs), operation='list folder')
            raw_items = page.get('files', []) if isinstance(page, Mapping) else []
            if isinstance(raw_items, list):
                if len(raw_items) > _MAX_PROVIDER_PAGE_ENTRIES:
                    raise GoogleDriveAPIError('Google Drive list folder exceeded the provider page processing limit')
                for raw in raw_items:
                    if not isinstance(raw, Mapping):
                        continue
                    if len(items) >= page_size:
                        item_id = raw.get('id')
                        if not isinstance(item_id, str) or not item_id or len(item_id) > _MAX_ITEM_REF_LENGTH:
                            raise GoogleDriveAPIError('Google Drive list folder returned invalid metadata')
                        pending_refs.append(item_id)
                        continue
                    self._assert_within_root(
                        context,
                        item=raw,
                        canonical_root_id=canonical_root_id,
                    )
                    self._assert_direct_child(raw, folder_ref=selected_ref)
                    items.append(self._normalize_item(raw, root_alias=configured_root.id))
            next_token = page.get('nextPageToken') if isinstance(page, Mapping) else None
            return {
                'items': items,
                'next_cursor': self._encode_cursor(
                    root=configured_root,
                    operation='list_folder',
                    query=None,
                    kinds=(),
                    provider_cursor=self._provider_state_value(
                        next_token if isinstance(next_token, str) and next_token else None,
                        pending_refs,
                    ),
                    resource_ref=selected_ref,
                    resource_default=folder_default,
                ),
            }
        finally:
            context.clear()
            root_item = None
            selected_folder = None
            page = None
            raw_items = None
            raw = None
            del context

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch one item after current ancestry reaches the selected root."""
        if not isinstance(item_ref, str) or not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH:
            raise GoogleDriveConfigError('item_ref must be a non-empty string')
        configured_root = self._root(root)
        context = _OperationContext(service=self._service())
        root_item: Any = None
        item: Any = None
        try:
            root_item = self._resolve_root(context, root=configured_root)
            if item_ref in (configured_root.file_id, root_item['id']):
                item = root_item
            else:
                item = self._execute(
                    context.service.files().get(
                        fileId=item_ref,
                        fields=DRIVE_FIELDS,
                        supportsAllDrives=True,
                    ),
                    operation='get metadata',
                )
            self._assert_within_root(
                context,
                item=item,
                canonical_root_id=root_item['id'],
            )
            return {'item': self._normalize_item(item, root_alias=configured_root.id)}
        finally:
            context.clear()
            root_item = None
            item = None
            del context

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Run bounded native search and discard results outside the selected root."""
        page_size = self._validate_max_results(max_results)
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            raise GoogleDriveConfigError('query must be a string')
        normalized_kinds = self._validate_kinds(kinds)
        configured_root = self._root(root)
        provider_cursor = self._decode_cursor(
            cursor,
            root=configured_root,
            operation='search',
            query=query,
            kinds=normalized_kinds,
        )
        context = _OperationContext(service=self._service())
        try:
            return self._search_with_context(
                context,
                configured_root=configured_root,
                query=query,
                normalized_kinds=normalized_kinds,
                provider_cursor=provider_cursor,
                page_size=page_size,
            )
        finally:
            context.clear()
            del context

    def _search_with_context(
        self,
        context: _OperationContext,
        *,
        configured_root: GoogleDriveRoot,
        query: str,
        normalized_kinds: tuple[str, ...],
        provider_cursor: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        """Search with one shared ancestry cache and operation-local provider service."""
        root_item: Any = None
        page: Any = None
        raw_items: Any = None
        raw: Any = None
        try:
            root_item = self._resolve_root(context, root=configured_root)
            if root_item.get('mimeType') != _FOLDER_MIME_TYPE:
                raise GoogleDriveConfigError('selected Google Drive root is not a folder')
            query_text = _escape_drive_query(query)
            clauses = [f"trashed = false and (name contains '{query_text}' or fullText contains '{query_text}')"]
            if normalized_kinds == ('folder',):
                clauses.append(f"mimeType = '{_FOLDER_MIME_TYPE}'")
            elif normalized_kinds == ('file',):
                clauses.extend(
                    (
                        f"mimeType != '{_FOLDER_MIME_TYPE}'",
                        f"mimeType != '{_SHORTCUT_MIME_TYPE}'",
                    )
                )
            elif normalized_kinds == ('file', 'folder'):
                clauses.append(f"mimeType != '{_SHORTCUT_MIME_TYPE}'")
            drive_query = ' and '.join(clauses)
            items: list[dict[str, Any]] = []
            next_token = provider_cursor
            for _page_number in range(_MAX_PROVIDER_PAGES):
                # Asking only for unused result capacity ensures the provider token never
                # advances past an authorized result that this invocation cannot return.
                remaining_results = page_size - len(items)
                kwargs = {
                    **self._list_kwargs(configured_root),
                    'q': drive_query,
                    'fields': f'nextPageToken,files({DRIVE_FIELDS})',
                    'pageSize': remaining_results,
                }
                if next_token is not None:
                    kwargs['pageToken'] = next_token
                page = self._execute(context.service.files().list(**kwargs), operation='search metadata')
                raw_items = page.get('files', []) if isinstance(page, Mapping) else []
                if isinstance(raw_items, list):
                    if len(raw_items) > _MAX_PROVIDER_PAGE_ENTRIES:
                        raise GoogleDriveAPIError('Google Drive search exceeded the provider page processing limit')
                    for raw in raw_items:
                        if not isinstance(raw, Mapping):
                            continue
                        mime_type = raw.get('mimeType')
                        candidate_kind = (
                            'folder'
                            if mime_type == _FOLDER_MIME_TYPE
                            else 'shortcut' if mime_type == _SHORTCUT_MIME_TYPE else 'file'
                        )
                        try:
                            self._assert_within_root(
                                context,
                                item=raw,
                                canonical_root_id=root_item['id'],
                            )
                        except GoogleDriveOutsideRootError:
                            continue
                        if normalized_kinds and candidate_kind not in normalized_kinds:
                            continue
                        items.append(self._normalize_item(raw, root_alias=configured_root.id))
                        if len(items) >= page_size:
                            break
                raw_next_token = page.get('nextPageToken') if isinstance(page, Mapping) else None
                next_token = raw_next_token if isinstance(raw_next_token, str) and raw_next_token else None
                if len(items) >= page_size or next_token is None:
                    break
            return {
                'items': items,
                'next_cursor': self._encode_cursor(
                    root=configured_root,
                    operation='search',
                    query=query,
                    kinds=normalized_kinds,
                    provider_cursor=next_token,
                ),
            }
        finally:
            root_item = None
            page = None
            raw_items = None
            raw = None
            del context

    def _execute(self, request: Any, *, operation: str) -> Any:
        """Execute with one bounded retry for 429 and transient server responses."""
        try:
            for attempt in range(2):
                mapped_failure: GoogleDriveError | None = None
                try:
                    return request.execute()
                except GoogleDriveError:
                    raise
                except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                    status = _status(exc)
                    if attempt == 0 and (status == 429 or status in _TRANSIENT_STATUSES):
                        self._sleep_fn(_retry_after(exc))
                        continue
                    mapped_failure = _map_failure(exc, operation=operation)
                if mapped_failure is not None:
                    # Raise after the except block so HttpError content is not traceback-reachable.
                    raise mapped_failure from None
            raise GoogleDriveAPIError(f'Google Drive {operation} failed')
        finally:
            request = None
