# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django-free Dropbox metadata client foundation.

Credentials and SDK objects exist only for one public operation. The client retains lazy
suppliers, injectable factories, and validated non-secret configuration.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import posixpath
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import partial
from typing import Any

import dropbox
from libs.clients.dropbox.config import (
    DropboxConfig,
    DropboxRoot,
    is_path_within,
    normalize_dropbox_path,
    parse_dropbox_config,
)
from libs.clients.dropbox.errors import (
    DropboxAPIError,
    DropboxAuthError,
    DropboxConfigError,
    DropboxError,
    DropboxForbiddenError,
    DropboxInvalidCursorError,
    DropboxNotFoundError,
    DropboxOutsideRootError,
    DropboxRateLimitedError,
)

DropboxSDKFactory = Callable[[str], Any]

_MAX_RETRY_AFTER_SECONDS = 60.0
_MAX_RESULTS = 100
_MAX_PROVIDER_PAGES = 5
_MAX_ENCODED_CURSOR_LENGTH = 131_072
_MAX_PROVIDER_STATE_LENGTH = 65_536
_MAX_PROVIDER_CURSOR_LENGTH = 16_384
_MAX_PENDING_REFS = 200
# Opaque Dropbox IDs and paths are normally far shorter; 4 KiB remains compatible
# while bounding caller-controlled SDK requests and cursor buffering.
_MAX_ITEM_REF_LENGTH = 4_096
_MAX_ROOT_ALIAS_LENGTH = 256
# Dropbox search accepts at most 1,000 UTF-8 characters in normal API usage.
_MAX_QUERY_LENGTH = 1_000
_MAX_CURSOR_FIELD_LENGTH = 4_096
_MAX_PROVIDER_PAGE_ENTRIES = 500
_TRANSIENT_TRANSPORT_FAILURES = (OSError,)


def _sdk_path(path: str) -> str:
    """Translate configured slash to the Dropbox SDK's empty root locator."""
    return '' if path == '/' else path


def _credential_field(info: Mapping[str, Any], field: str) -> str:
    """Return one required non-empty string from parsed credential JSON."""
    value = info.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DropboxAuthError('Dropbox refresh credential is incomplete')
    return value.strip()


def _build_sdk(raw_credential: str) -> Any:
    """Build the official Dropbox SDK from a complete refresh credential JSON value."""
    info: Any = None
    app_key: str | None = None
    app_secret: str | None = None
    refresh_token: str | None = None
    sdk: Any = None
    try:
        invalid_json = False
        try:
            info = json.loads(raw_credential)
        except (TypeError, ValueError):
            invalid_json = True
        if invalid_json:
            raise DropboxAuthError('Dropbox refresh credential is not valid JSON') from None
        if not isinstance(info, Mapping):
            raise DropboxAuthError('Dropbox refresh credential must be a JSON object')
        app_key = _credential_field(info, 'app_key')
        app_secret = _credential_field(info, 'app_secret')
        refresh_token = _credential_field(info, 'refresh_token')
        build_failed = False
        try:
            sdk = dropbox.Dropbox(
                oauth2_refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret,
                max_retries_on_error=0,
                max_retries_on_rate_limit=0,
            )
        except DropboxError:
            raise
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            build_failed = True
        if build_failed:
            raise DropboxAuthError('failed to build Dropbox refresh credentials') from None
        return sdk
    finally:
        # Tracebacks retain frame locals, so overwrite every credential-bearing value.
        raw_credential = ''
        info = None
        app_key = None
        app_secret = None
        refresh_token = None
        sdk = None


def _status(provider_failure: Exception) -> int | None:
    """Extract a safe HTTP status from a Dropbox SDK failure."""
    status = getattr(provider_failure, 'status_code', None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _tagged(provider_value: Any, tag: str) -> bool:
    """Safely test one generated Dropbox union tag without formatting its value."""
    check = getattr(provider_value, f'is_{tag}', None)
    if not callable(check):
        return False
    try:
        return check() is True
    except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
        return False


def _api_failure_kind(provider_failure: Exception) -> str | None:
    """Recognize common route-level permission and missing-path union tags."""
    if not isinstance(provider_failure, dropbox.exceptions.ApiError):
        return None
    pending = [getattr(provider_failure, 'error', None)]
    visited: set[int] = set()
    for _depth in range(4):
        next_pending: list[Any] = []
        for value in pending:
            if value is None or id(value) in visited:
                continue
            visited.add(id(value))
            if _tagged(value, 'not_found'):
                return 'not_found'
            if any(_tagged(value, tag) for tag in ('no_permission', 'no_write_permission', 'insufficient_permissions')):
                return 'forbidden'
            for tag in ('path', 'from_lookup', 'to', 'reason'):
                if not _tagged(value, tag):
                    continue
                getter = getattr(value, f'get_{tag}', None)
                if callable(getter):
                    nested_value = None
                    try:
                        nested_value = getter()
                    except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                        nested_value = None
                    if nested_value is not None:
                        next_pending.append(nested_value)
        pending = next_pending
    return None


def _path_root_failure_kind(provider_failure: Exception) -> str | None:
    """Recognize namespace path-root failures outside the route ApiError hierarchy."""
    if not isinstance(provider_failure, dropbox.exceptions.PathRootError):
        return None
    error = getattr(provider_failure, 'error', None)
    return 'forbidden' if _tagged(error, 'no_permission') else None


def _retry_after(provider_failure: Exception) -> float:
    """Return a finite positive Dropbox backoff capped at sixty seconds."""
    value = getattr(provider_failure, 'backoff', None)
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(delay) or delay <= 0:
        return 0.0
    return min(delay, _MAX_RETRY_AFTER_SECONDS)


def _is_transient(provider_failure: Exception) -> bool:
    """Identify transport and server failures eligible for the single retry."""
    if isinstance(provider_failure, _TRANSIENT_TRANSPORT_FAILURES):
        return True
    status = _status(provider_failure)
    return status is not None and 500 <= status <= 599


def _map_failure(provider_failure: Exception, *, operation_name: str) -> DropboxError:
    """Translate a provider failure into a typed failure with a safe message."""
    context = f'Dropbox {operation_name} failed'
    status = _status(provider_failure)
    if isinstance(provider_failure, dropbox.exceptions.AuthError) or status == 401:
        return DropboxAuthError(f'{context} (authentication)')
    if isinstance(provider_failure, dropbox.exceptions.RateLimitError) or status == 429:
        return DropboxRateLimitedError(f'{context} (rate limited)')
    failure_kind = _path_root_failure_kind(provider_failure) or _api_failure_kind(provider_failure)
    if status == 403 or failure_kind == 'forbidden':
        return DropboxForbiddenError(f'{context} (forbidden)')
    if status == 404 or failure_kind == 'not_found':
        return DropboxNotFoundError(f'{context} (not found)')
    return DropboxAPIError(context, status=status)


class DropboxClient:
    """Build operation-local Dropbox SDKs and normalize metadata-only responses."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
        sdk_factory: DropboxSDKFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """Create a metadata client without resolving or retaining Dropbox secrets."""
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise DropboxConfigError('instance_id must be a non-empty string')
        self._token_supplier = token_supplier
        self._config: DropboxConfig = parse_dropbox_config(config or {})
        self._instance_id = instance_id.strip()
        self._sdk_factory = sdk_factory or _build_sdk
        self._sleep_fn = sleep_fn or time.sleep

    def _sdk(self) -> Any:
        """Resolve a refresh credential and build one optionally namespaced SDK."""
        raw_credential: str | None = None
        sdk: Any = None
        try:
            raw_credential = self._token_supplier()
            if not raw_credential:
                raise DropboxAuthError('no Dropbox refresh credential resolved')
            factory_failed = False
            try:
                sdk = self._sdk_factory(raw_credential)
                if self._config.namespace_id is not None:
                    path_root = dropbox.common.PathRoot.namespace_id(self._config.namespace_id)
                    sdk = sdk.with_path_root(path_root)
                return sdk
            except DropboxError:
                failed_sdk = sdk
                sdk = None
                if failed_sdk is not None:
                    self._close_sdk(failed_sdk)
                failed_sdk = None
                raise
            except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                failed_sdk = sdk
                sdk = None
                if failed_sdk is not None:
                    self._close_sdk(failed_sdk)
                failed_sdk = None
                factory_failed = True
            if factory_failed:
                raise DropboxAuthError('failed to build Dropbox refresh credentials') from None
            raise DropboxAuthError('failed to build Dropbox refresh credentials') from None
        finally:
            raw_credential = None
            sdk = None

    def _normalize_item(self, raw: Any, *, root_alias: str) -> dict[str, Any]:
        """Return the metadata-only cross-provider shape for one Dropbox item."""
        path_display: Any = None
        server_modified: Any = None
        normalized_path: Any = None
        parent: Any = None
        parent_refs: Any = None
        modified_at: Any = None
        provider_metadata: Any = None
        rev: Any = None
        result: dict[str, Any] | None = None
        normalization_failed = False
        try:
            is_file = isinstance(raw, dropbox.files.FileMetadata)
            if not is_file and not isinstance(raw, dropbox.files.FolderMetadata):
                raise TypeError
            path_display = raw.path_display
            if not isinstance(path_display, str):
                raise TypeError
            # Dropbox can return the namespace root as an empty path; expose it canonically.
            normalized_path = '/' if path_display == '' else path_display
            if not normalized_path.startswith('/'):
                raise TypeError
            parent = posixpath.dirname(normalized_path)
            parent_refs = [] if normalized_path == '/' else [parent or '/']
            server_modified = raw.server_modified if is_file else None
            modified_at = server_modified.isoformat() if isinstance(server_modified, datetime) else None
            provider_metadata = {}
            if is_file:
                rev = raw.rev
                if isinstance(rev, str) and rev:
                    provider_metadata['rev'] = rev
            result = {
                'provider': 'dropbox',
                'root': root_alias,
                'id': raw.id,
                'name': raw.name,
                'kind': 'file' if is_file else 'folder',
                'mime_type': None,
                'size': raw.size if is_file else None,
                'modified_at': modified_at,
                'parent_refs': parent_refs,
                'path': normalized_path,
                'web_url': None,
                'provider_metadata': provider_metadata,
            }
        except (AttributeError, TypeError, ValueError, OverflowError):
            normalization_failed = True
        finally:
            del raw
            path_display = None
            server_modified = None
            normalized_path = None
            parent = None
            parent_refs = None
            modified_at = None
            provider_metadata = None
            rev = None
        if normalization_failed:
            raise DropboxAPIError('Dropbox returned invalid metadata') from None
        if result is None:  # pragma: no cover - all uncaught paths assign or raise
            raise DropboxAPIError('Dropbox returned invalid metadata')
        return result

    def _synthetic_root(self, root: DropboxRoot) -> dict[str, Any]:
        """Represent the unsupported metadata endpoint root from its configured locator."""
        return {
            'provider': 'dropbox',
            'root': root.id,
            'id': root.path,
            'name': root.path,
            'kind': 'folder',
            'mime_type': None,
            'size': None,
            'modified_at': None,
            'parent_refs': [],
            'path': root.path,
            'web_url': None,
            'provider_metadata': {},
        }

    def _validate_max_results(self, max_results: int) -> int:
        """Require an integer provider page size from one through one hundred."""
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= _MAX_RESULTS:
            raise DropboxConfigError('max_results must be an integer from 1 through 100')
        return max_results

    def _root(self, alias: str) -> DropboxRoot:
        """Select a configured root alias without provider interaction."""
        if not isinstance(alias, str) or not alias or len(alias) > _MAX_ROOT_ALIAS_LENGTH:
            raise DropboxConfigError('root must identify a configured alias')
        for configured_root in self._config.roots:
            if configured_root.id == alias:
                return configured_root
        raise DropboxConfigError(f'unknown Dropbox root: {alias}')

    def _path_lower(self, raw: Any) -> str:
        """Read and structurally validate provider-authoritative path_lower verbatim."""
        try:
            path_lower = getattr(raw, 'path_lower', None)
            if path_lower == '':
                return '/'
            if not isinstance(path_lower, str):
                raise DropboxAPIError('Dropbox returned metadata without an authoritative path')
            try:
                # Validation rejects ambiguous separators but does not lowercase or casefold.
                normalize_dropbox_path(path_lower)
            except DropboxConfigError:
                raise DropboxAPIError('Dropbox returned invalid authoritative path metadata') from None
            return path_lower
        finally:
            raw = None

    def _resolve_root(self, sdk: Any, *, root: DropboxRoot) -> tuple[Any | None, str]:
        """Resolve current configured-root metadata and return its authoritative path."""
        if root.path == '/':
            return None, '/'
        raw = self._execute(
            lambda: sdk.files_get_metadata(root.path),
            operation_name='resolve root',
        )
        path_lower = self._path_lower(raw)
        return raw, path_lower

    def _assert_within_root(self, raw: Any, *, root_path_lower: str) -> None:
        """Authorize metadata only from provider-returned path_lower segments."""
        candidate = self._path_lower(raw)
        if not is_path_within(root_path_lower, candidate):
            raise DropboxOutsideRootError('Dropbox item is outside the configured root')

    def _assert_direct_child(self, raw: Any, *, folder_path_lower: str) -> None:
        """Require the current authoritative parent path to equal the selected folder."""
        candidate = self._path_lower(raw)
        if posixpath.dirname(candidate) != folder_path_lower:
            raise DropboxOutsideRootError('Dropbox item is no longer a direct child of the selected folder')

    def _validate_kinds(self, kinds: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize supported kinds into a stable cursor-binding order."""
        if not isinstance(kinds, tuple) or len(kinds) > 2 or any(kind not in {'file', 'folder'} for kind in kinds):
            raise DropboxConfigError('kinds must contain only file or folder')
        return tuple(sorted(set(kinds)))

    def _metadata_list(self, value: Any, *, operation: str) -> list[Any]:
        """Require a provider metadata list while clearing malformed values on failure."""
        try:
            if not isinstance(value, list):
                raise DropboxAPIError(f'Dropbox {operation} returned invalid metadata')
            if len(value) > _MAX_PROVIDER_PAGE_ENTRIES:
                raise DropboxAPIError(f'Dropbox {operation} exceeded the provider page processing limit')
            return value
        finally:
            value = None

    def _continuation_cursor(self, page: Any, *, operation: str) -> str | None:
        """Require a usable provider cursor whenever a page declares more results."""
        try:
            has_more = getattr(page, 'has_more', None)
            if not isinstance(has_more, bool):
                raise DropboxAPIError(f'Dropbox {operation} returned invalid pagination metadata')
            if not has_more:
                return None
            cursor = getattr(page, 'cursor', None)
            if not isinstance(cursor, str) or not cursor:
                raise DropboxAPIError(f'Dropbox {operation} omitted its continuation cursor')
            if len(cursor) > _MAX_PROVIDER_CURSOR_LENGTH:
                raise DropboxAPIError(f'Dropbox {operation} returned an oversized continuation cursor')
            return cursor
        finally:
            page = None

    def _encode_cursor(
        self,
        *,
        root: DropboxRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        provider_cursor: str | None,
        resource_ref: str | None = None,
        resource_default: bool | None = None,
    ) -> str | None:
        """Wrap a provider token in strict URL-safe JSON operation bindings."""
        if not provider_cursor:
            return None
        if len(provider_cursor) > _MAX_PROVIDER_STATE_LENGTH:
            raise DropboxAPIError('Dropbox cursor state exceeds the safe size limit')
        binding_values = (self._instance_id, root.id, root.path, operation)
        if (
            any(len(value) > _MAX_CURSOR_FIELD_LENGTH for value in binding_values)
            or (query is not None and len(query) > _MAX_CURSOR_FIELD_LENGTH)
            or (resource_ref is not None and len(resource_ref) > _MAX_CURSOR_FIELD_LENGTH)
        ):
            raise DropboxAPIError('Dropbox cursor binding exceeds the safe field size limit')
        payload: dict[str, Any] = {
            'v': 1,
            'instance': self._instance_id,
            'root': root.id,
            'root_locator': root.path,
            'operation': operation,
            'query': query,
            'kinds': list(kinds),
            'provider_cursor': provider_cursor,
        }
        if operation == 'list_folder':
            payload['resource_ref'] = resource_ref
            payload['resource_default'] = resource_default
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
        result = encoded.rstrip(b'=').decode('ascii')
        if len(result) > _MAX_ENCODED_CURSOR_LENGTH:
            raise DropboxAPIError('Dropbox cursor exceeds the safe size limit')
        return result

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        root: DropboxRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        resource_ref: str | None = None,
        resource_default: bool | None = None,
    ) -> str | None:
        """Reject malformed or rebound cursor envelopes before SDK construction."""
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_ENCODED_CURSOR_LENGTH:
            raise DropboxInvalidCursorError('Dropbox cursor is invalid')
        try:
            payload = json.loads(
                base64.b64decode(
                    cursor + '=' * (-len(cursor) % 4),
                    altchars=b'-_',
                    validate=True,
                ).decode('utf-8')
            )
        except (binascii.Error, RecursionError, UnicodeError, ValueError):
            raise DropboxInvalidCursorError('Dropbox cursor is invalid') from None
        expected_fields = {
            'v',
            'instance',
            'root',
            'root_locator',
            'operation',
            'query',
            'kinds',
            'provider_cursor',
        }
        if operation == 'list_folder':
            expected_fields.update({'resource_ref', 'resource_default'})
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise DropboxInvalidCursorError('Dropbox cursor is invalid')
        version = payload.get('v')
        cursor_kinds = payload.get('kinds')
        strings = ('instance', 'root', 'root_locator', 'operation')
        raw_provider_cursor = payload.get('provider_cursor')
        valid = (
            isinstance(version, int)
            and not isinstance(version, bool)
            and version == 1
            and all(isinstance(payload.get(field), str) and payload.get(field) for field in strings)
            and isinstance(raw_provider_cursor, str)
            and bool(raw_provider_cursor)
            and len(raw_provider_cursor) <= _MAX_PROVIDER_STATE_LENGTH
            and (payload.get('query') is None or isinstance(payload.get('query'), str))
            and isinstance(cursor_kinds, list)
            and all(isinstance(kind, str) for kind in cursor_kinds)
            and all(len(payload[field]) <= _MAX_CURSOR_FIELD_LENGTH for field in strings)
            and (payload.get('query') is None or len(payload['query']) <= _MAX_CURSOR_FIELD_LENGTH)
            and len(cursor_kinds) <= 2
            and all(len(kind) <= _MAX_CURSOR_FIELD_LENGTH for kind in cursor_kinds)
        )
        if operation == 'list_folder':
            valid = (
                valid
                and (payload.get('resource_ref') is None or isinstance(payload.get('resource_ref'), str))
                and isinstance(payload.get('resource_default'), bool)
                and (payload.get('resource_ref') is None or len(payload['resource_ref']) <= _MAX_CURSOR_FIELD_LENGTH)
            )
        if not valid:
            raise DropboxInvalidCursorError('Dropbox cursor is invalid')
        matches_binding = (
            payload['instance'] == self._instance_id
            and payload['root'] == root.id
            and payload['root_locator'] == root.path
            and payload['operation'] == operation
            and payload['query'] == query
            and cursor_kinds == list(kinds)
        )
        if operation == 'list_folder':
            matches_binding = (
                matches_binding
                and payload['resource_ref'] == resource_ref
                and payload['resource_default'] == resource_default
            )
        if not matches_binding:
            raise DropboxInvalidCursorError('Dropbox cursor does not match this operation')
        provider_cursor = payload['provider_cursor']
        return provider_cursor if isinstance(provider_cursor, str) else None

    def _close_sdk(self, sdk: Any) -> None:
        """Best-effort close one operation SDK without replacing its operation result."""
        try:
            close = getattr(sdk, 'close', None)
            if callable(close):
                close()
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            return
        finally:
            sdk = None

    def _execute(self, call: Callable[[], Any], *, operation_name: str) -> Any:
        """Execute with one bounded retry for rate and transient transport failures."""
        try:
            for attempt in range(2):
                mapped_failure: DropboxError | None = None
                try:
                    return call()
                except DropboxError:
                    raise
                except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                    retryable = isinstance(exc, dropbox.exceptions.RateLimitError) or _is_transient(exc)
                    if attempt == 0 and retryable:
                        self._sleep_fn(_retry_after(exc))
                        continue
                    mapped_failure = _map_failure(exc, operation_name=operation_name)
                if mapped_failure is not None:
                    raise mapped_failure from None
            raise DropboxAPIError(f'Dropbox {operation_name} failed')
        finally:
            call = None  # type: ignore[assignment]

    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured Dropbox roots only."""
        sdk: Any = self._sdk()
        metadata: Any = None
        call: Callable[[], Any] | None = None
        try:
            items = []
            for root in self._config.roots:
                if root.path == '/':
                    items.append(self._synthetic_root(root))
                    continue
                call = lambda path=_sdk_path(root.path): sdk.files_get_metadata(path)
                metadata = self._execute(call, operation_name='resolve root')
                self._assert_within_root(metadata, root_path_lower=self._path_lower(metadata))
                items.append(self._normalize_item(metadata, root_alias=root.id))
                metadata = None
                call = None
            return {'items': items, 'next_cursor': None}
        finally:
            closing_sdk = sdk
            sdk = None
            metadata = None
            call = None
            if closing_sdk is not None:
                self._close_sdk(closing_sdk)
            closing_sdk = None

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List direct children after current root and folder path authorization."""
        page_size = self._validate_max_results(max_results)
        configured_root = self._root(root)
        if folder_ref is not None and (
            not isinstance(folder_ref, str) or not folder_ref or len(folder_ref) > _MAX_ITEM_REF_LENGTH
        ):
            raise DropboxConfigError('folder_ref must be a non-empty string')
        resource_default = folder_ref is None
        resource_ref = configured_root.path if resource_default else folder_ref
        encoded_state = self._decode_cursor(
            cursor,
            root=configured_root,
            operation='list_folder',
            query=None,
            kinds=(),
            resource_ref=resource_ref,
            resource_default=resource_default,
        )
        provider_cursor, pending_refs = self._provider_state(encoded_state)
        sdk: Any = self._sdk()
        root_item: Any = None
        selected: Any = None
        page: Any = None
        entries: list[Any] | None = None
        entry: Any = None
        pending_item: Any = None
        try:
            root_item, root_path_lower = self._resolve_root(sdk, root=configured_root)
            if root_item is not None and not isinstance(root_item, dropbox.files.FolderMetadata):
                raise DropboxConfigError('selected Dropbox root is not a folder')
            if resource_default:
                selected = root_item
                selected_path = configured_root.path
                selected_path_lower = root_path_lower
            else:
                selected = self._execute(
                    lambda: sdk.files_get_metadata(folder_ref),
                    operation_name='get folder metadata',
                )
                self._assert_within_root(selected, root_path_lower=root_path_lower)
                selected_path_value = getattr(selected, 'path_display', None)
                if not isinstance(selected_path_value, str):
                    raise DropboxAPIError('Dropbox returned invalid folder metadata')
                selected_path = selected_path_value
                selected_path_lower = self._path_lower(selected)
            if selected is not None and not isinstance(selected, dropbox.files.FolderMetadata):
                raise DropboxConfigError('selected Dropbox root or item is not a folder')
            items: list[dict[str, Any]] = []
            while pending_refs and len(items) < page_size:
                item_ref = pending_refs.pop(0)
                pending_call: Callable[[], Any] = partial(sdk.files_get_metadata, item_ref)
                pending_item = self._execute(pending_call, operation_name='resume folder listing')
                pending_call = None  # type: ignore[assignment]
                self._assert_within_root(pending_item, root_path_lower=root_path_lower)
                self._assert_direct_child(pending_item, folder_path_lower=selected_path_lower)
                items.append(self._normalize_item(pending_item, root_alias=configured_root.id))
                pending_item = None
            if len(items) >= page_size:
                return {
                    'items': items,
                    'next_cursor': self._encode_cursor(
                        root=configured_root,
                        operation='list_folder',
                        query=None,
                        kinds=(),
                        provider_cursor=self._provider_state_value(provider_cursor, pending_refs),
                        resource_ref=resource_ref,
                        resource_default=resource_default,
                    ),
                }
            if cursor is not None and provider_cursor is None:
                return {'items': items, 'next_cursor': None}
            if cursor is None:
                page = self._execute(
                    lambda: sdk.files_list_folder(
                        _sdk_path(selected_path),
                        recursive=False,
                        limit=page_size,
                    ),
                    operation_name='list folder',
                )
            else:
                page = self._execute(
                    lambda: sdk.files_list_folder_continue(provider_cursor),
                    operation_name='continue folder listing',
                )
            entries = self._metadata_list(getattr(page, 'entries', None), operation='list folder')
            for entry in entries if entries is not None else []:
                self._assert_within_root(entry, root_path_lower=root_path_lower)
                self._assert_direct_child(entry, folder_path_lower=selected_path_lower)
                if len(items) < page_size:
                    items.append(self._normalize_item(entry, root_alias=configured_root.id))
                    continue
                item_id = getattr(entry, 'id', None)
                if not isinstance(item_id, str) or not item_id:
                    raise DropboxAPIError('Dropbox list folder returned invalid metadata')
                pending_refs.append(item_id)
            next_provider = self._continuation_cursor(page, operation='list folder')
            return {
                'items': items,
                'next_cursor': self._encode_cursor(
                    root=configured_root,
                    operation='list_folder',
                    query=None,
                    kinds=(),
                    provider_cursor=self._provider_state_value(
                        next_provider if isinstance(next_provider, str) else None,
                        pending_refs,
                    ),
                    resource_ref=resource_ref,
                    resource_default=resource_default,
                ),
            }
        finally:
            closing_sdk = sdk
            sdk = None
            root_item = None
            selected = None
            page = None
            entries = None
            entry = None
            pending_item = None
            self._close_sdk(closing_sdk)
            closing_sdk = None

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch current provider metadata and authorize its authoritative path."""
        if not isinstance(item_ref, str) or not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH:
            raise DropboxConfigError('item_ref must be a non-empty string')
        configured_root = self._root(root)
        sdk: Any = self._sdk()
        _root_item: Any = None
        item: Any = None
        try:
            _root_item, root_path_lower = self._resolve_root(sdk, root=configured_root)
            if configured_root.path == '/' and item_ref == '/':
                return {'item': self._synthetic_root(configured_root)}
            item = self._execute(
                lambda: sdk.files_get_metadata(item_ref),
                operation_name='get metadata',
            )
            self._assert_within_root(item, root_path_lower=root_path_lower)
            return {'item': self._normalize_item(item, root_alias=configured_root.id)}
        finally:
            closing_sdk = sdk
            sdk = None
            _root_item = None
            item = None
            self._close_sdk(closing_sdk)
            closing_sdk = None

    def _provider_state(self, value: str | None) -> tuple[str | None, list[str]]:
        """Decode an internal provider token and ordered overrun references."""
        if value is None:
            return None, []
        if len(value) > _MAX_PROVIDER_STATE_LENGTH:
            raise DropboxInvalidCursorError('Dropbox cursor state is invalid')
        try:
            state = json.loads(value)
        except (RecursionError, ValueError):
            raise DropboxInvalidCursorError('Dropbox search cursor state is invalid') from None
        if not isinstance(state, dict) or set(state) != {'cursor', 'pending'}:
            raise DropboxInvalidCursorError('Dropbox search cursor state is invalid')
        provider_cursor = state.get('cursor')
        pending = state.get('pending')
        if (
            (provider_cursor is not None and (not isinstance(provider_cursor, str) or not provider_cursor))
            or not isinstance(pending, list)
            or len(pending) > _MAX_PENDING_REFS
            or any(not isinstance(item_ref, str) or not item_ref for item_ref in pending)
            or any(len(item_ref) > _MAX_ITEM_REF_LENGTH for item_ref in pending if isinstance(item_ref, str))
            or (isinstance(provider_cursor, str) and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
        ):
            raise DropboxInvalidCursorError('Dropbox search cursor state is invalid')
        return provider_cursor, list(pending)

    def _provider_state_value(self, provider_cursor: str | None, pending: list[str]) -> str | None:
        """Encode provider continuation plus unconsumed ordered item references."""
        if provider_cursor is None and not pending:
            return None
        if (
            (provider_cursor is not None and len(provider_cursor) > _MAX_PROVIDER_CURSOR_LENGTH)
            or len(pending) > _MAX_PENDING_REFS
            or any(not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH for item_ref in pending)
        ):
            raise DropboxAPIError('Dropbox pagination state exceeds the safe buffering limit')
        value = json.dumps(
            {'cursor': provider_cursor, 'pending': pending},
            separators=(',', ':'),
            sort_keys=True,
        )
        if len(value) > _MAX_PROVIDER_STATE_LENGTH:
            raise DropboxAPIError('Dropbox pagination state exceeds the safe size limit')
        return value

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Run bounded native search and discard every result outside the root."""
        page_size = self._validate_max_results(max_results)
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            raise DropboxConfigError('query must be a string')
        normalized_kinds = self._validate_kinds(kinds)
        configured_root = self._root(root)
        encoded_state = self._decode_cursor(
            cursor,
            root=configured_root,
            operation='search',
            query=query,
            kinds=normalized_kinds,
        )
        provider_cursor, pending_refs = self._provider_state(encoded_state)
        sdk: Any = self._sdk()
        root_item: Any = None
        page: Any = None
        matches: list[Any] | None = None
        match: Any = None
        raw: Any = None
        raw_pending: Any = None
        try:
            root_item, root_path_lower = self._resolve_root(sdk, root=configured_root)
            if root_item is not None and not isinstance(root_item, dropbox.files.FolderMetadata):
                raise DropboxConfigError('selected Dropbox root is not a folder')
            items: list[dict[str, Any]] = []
            while pending_refs and len(items) < page_size:
                item_ref = pending_refs.pop(0)
                pending_call: Callable[[], Any] = lambda: sdk.files_get_metadata(item_ref)
                raw_pending = self._execute(pending_call, operation_name='resume metadata search')
                pending_call = None  # type: ignore[assignment]
                try:
                    self._assert_within_root(raw_pending, root_path_lower=root_path_lower)
                except DropboxOutsideRootError:
                    raw_pending = None
                    continue
                pending_kind = (
                    'file'
                    if isinstance(raw_pending, dropbox.files.FileMetadata)
                    else 'folder' if isinstance(raw_pending, dropbox.files.FolderMetadata) else None
                )
                if pending_kind is None:
                    raise DropboxAPIError('Dropbox search returned invalid metadata')
                if not normalized_kinds or pending_kind in normalized_kinds:
                    items.append(self._normalize_item(raw_pending, root_alias=configured_root.id))
            if len(items) >= page_size:
                return {
                    'items': items,
                    'next_cursor': self._encode_cursor(
                        root=configured_root,
                        operation='search',
                        query=query,
                        kinds=normalized_kinds,
                        provider_cursor=self._provider_state_value(provider_cursor, pending_refs),
                    ),
                }
            if cursor is not None and provider_cursor is None:
                return {'items': items, 'next_cursor': None}
            next_provider = provider_cursor
            for page_number in range(_MAX_PROVIDER_PAGES):
                remaining = page_size - len(items)
                if page_number == 0 and cursor is None:
                    options = dropbox.files.SearchOptions(
                        path=_sdk_path(configured_root.path),
                        max_results=remaining,
                    )
                    search_call: Callable[[], Any] = partial(sdk.files_search_v2, query, options=options)
                    page = self._execute(search_call, operation_name='search metadata')
                    search_call = None  # type: ignore[assignment]
                else:
                    continue_call: Callable[[], Any] = partial(sdk.files_search_continue_v2, next_provider)
                    page = self._execute(continue_call, operation_name='continue metadata search')
                    continue_call = None  # type: ignore[assignment]
                matches = self._metadata_list(getattr(page, 'matches', None), operation='search')
                accepted_refs: list[str] = []
                for match in matches if matches is not None else []:
                    raw = self._search_match_metadata(match)
                    try:
                        self._assert_within_root(raw, root_path_lower=root_path_lower)
                    except DropboxOutsideRootError:
                        raw = None
                        continue
                    kind = (
                        'file'
                        if isinstance(raw, dropbox.files.FileMetadata)
                        else 'folder' if isinstance(raw, dropbox.files.FolderMetadata) else None
                    )
                    if kind is None:
                        raise DropboxAPIError('Dropbox search returned invalid metadata')
                    if normalized_kinds and kind not in normalized_kinds:
                        continue
                    item_id = getattr(raw, 'id', None)
                    if not isinstance(item_id, str) or not item_id:
                        raise DropboxAPIError('Dropbox search returned invalid metadata')
                    if len(items) < page_size:
                        items.append(self._normalize_item(raw, root_alias=configured_root.id))
                    else:
                        accepted_refs.append(item_id)
                next_provider = self._continuation_cursor(page, operation='search')
                if accepted_refs:
                    pending_refs.extend(accepted_refs)
                    break
                if len(items) >= page_size or next_provider is None:
                    break
            return {
                'items': items,
                'next_cursor': self._encode_cursor(
                    root=configured_root,
                    operation='search',
                    query=query,
                    kinds=normalized_kinds,
                    provider_cursor=self._provider_state_value(next_provider, pending_refs),
                ),
            }
        finally:
            closing_sdk = sdk
            sdk = None
            root_item = None
            page = None
            matches = None
            match = None
            raw = None
            raw_pending = None
            self._close_sdk(closing_sdk)
            closing_sdk = None

    def _search_match_metadata(self, match: Any) -> Any:
        """Safely extract file/folder metadata from a SearchMatchV2 wrapper."""
        metadata: Any = None
        try:
            metadata = getattr(match, 'metadata', None)
            getter = getattr(metadata, 'get_metadata', None)
            if callable(getter):
                try:
                    return getter()
                except (AttributeError, TypeError, ValueError):
                    pass
            if isinstance(metadata, (dropbox.files.FileMetadata, dropbox.files.FolderMetadata)):
                return metadata
            raise DropboxAPIError('Dropbox search returned invalid metadata')
        finally:
            match = None
            metadata = None
