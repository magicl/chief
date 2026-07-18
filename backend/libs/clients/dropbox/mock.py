# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Deterministic in-memory Dropbox metadata namespace."""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from typing import Any, Literal

from libs.clients.dropbox.client import (
    _MAX_ITEM_REF_LENGTH,
    _MAX_QUERY_LENGTH,
    DropboxClient,
)
from libs.clients.dropbox.config import (
    DropboxRoot,
    _ascii_lower,
    is_path_within,
    normalize_dropbox_path,
)
from libs.clients.dropbox.errors import (
    DropboxConfigError,
    DropboxNotFoundError,
    DropboxOutsideRootError,
)


class MockDropboxClient(DropboxClient):
    """Model root-safe Dropbox metadata behavior without SDK or content APIs."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
    ) -> None:
        """Create an in-memory namespace with the production constructor shape."""
        super().__init__(
            token_supplier=token_supplier,
            config=config,
            instance_id=instance_id,
        )
        self._items: dict[str, dict[str, Any]] = {}

    def seed_item(
        self,
        item_id: str,
        *,
        path: str,
        kind: Literal['file', 'folder'],
        size: int | None = None,
        rev: str | None = None,
        path_lower: str | None = None,
    ) -> dict[str, Any]:
        """Add or replace current metadata so tests can model provider-side moves."""
        if not isinstance(item_id, str) or not item_id:
            raise DropboxConfigError('mock Dropbox item ID must be a non-empty string')
        display_path = normalize_dropbox_path(path)
        authoritative_path = normalize_dropbox_path(path_lower) if path_lower is not None else display_path
        if kind not in {'file', 'folder'}:
            raise DropboxConfigError('mock Dropbox kind must be file or folder')
        if kind == 'folder' and (size is not None or rev is not None):
            raise DropboxConfigError('mock Dropbox folder cannot have file metadata')
        name = posixpath.basename(display_path)
        item = {
            'provider': 'dropbox',
            'root': None,
            'id': item_id,
            'name': name,
            'kind': kind,
            'mime_type': None,
            'size': size if kind == 'file' else None,
            'modified_at': None,
            'parent_refs': [] if display_path == '/' else [posixpath.dirname(display_path) or '/'],
            'path': display_path,
            'path_lower': authoritative_path,
            'web_url': None,
            'provider_metadata': {'rev': rev} if kind == 'file' and rev else {},
        }
        self._items[item_id] = item
        return self._result_item(item, root_alias='')

    def _mock_root(self, alias: str) -> tuple[DropboxRoot, dict[str, Any], str]:
        """Resolve a configured alias to current metadata and authoritative path."""
        root = self._root(alias)
        if root.path == '/':
            synthetic = self._synthetic_root(root)
            synthetic['path_lower'] = '/'
            return root, synthetic, '/'
        matches = [
            item
            for item in self._items.values()
            if isinstance(item.get('path'), str) and _ascii_lower(str(item['path'])) == _ascii_lower(root.path)
        ]
        if len(matches) != 1:
            raise DropboxNotFoundError('configured mock Dropbox root is not seeded')
        item = matches[0]
        path_lower = item.get('path_lower')
        if not isinstance(path_lower, str):
            raise DropboxNotFoundError('configured mock Dropbox root has invalid metadata')
        return root, item, path_lower

    def _authorize(self, item: Mapping[str, Any], *, root_path_lower: str) -> None:
        """Check current seeded provider paths with segment-safe containment."""
        candidate = item.get('path_lower')
        if not isinstance(candidate, str) or not is_path_within(root_path_lower, candidate):
            raise DropboxOutsideRootError('Dropbox item is outside the configured root')

    def _result_item(self, item: Mapping[str, Any], *, root_alias: str) -> dict[str, Any]:
        """Copy one seeded record while excluding mock-only authorization state."""
        result = {key: value for key, value in item.items() if key != 'path_lower'}
        result['root'] = root_alias
        result['parent_refs'] = list(item.get('parent_refs', []))
        result['provider_metadata'] = dict(item.get('provider_metadata', {}))
        return result

    def _page(
        self,
        items: list[dict[str, Any]],
        *,
        root: DropboxRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        cursor: str | None,
        max_results: int,
        root_path_lower: str,
        folder_path_lower: str | None = None,
        resource_ref: str | None = None,
        resource_default: bool | None = None,
    ) -> dict[str, Any]:
        """Paginate ordered IDs and reauthorize every buffered record on resume."""
        encoded_state = self._decode_cursor(
            cursor,
            root=root,
            operation=operation,
            query=query,
            kinds=kinds,
            resource_ref=resource_ref,
            resource_default=resource_default,
        )
        _provider_cursor, pending_refs = self._provider_state(encoded_state)
        if cursor is None:
            pending_refs = [str(item['id']) for item in items]
        page_items: list[dict[str, Any]] = []
        while pending_refs and len(page_items) < max_results:
            item = self._find_ref(pending_refs.pop(0))
            try:
                self._authorize(item, root_path_lower=root_path_lower)
            except DropboxOutsideRootError:
                if operation == 'search':
                    continue
                raise
            if operation == 'list_folder':
                candidate = item.get('path_lower')
                if not isinstance(candidate, str) or posixpath.dirname(candidate) != folder_path_lower:
                    raise DropboxOutsideRootError('Dropbox item is no longer a direct child of the selected folder')
            if operation == 'search' and kinds and item.get('kind') not in kinds:
                continue
            page_items.append(self._result_item(item, root_alias=root.id))
        return {
            'items': page_items,
            'next_cursor': self._encode_cursor(
                root=root,
                operation=operation,
                query=query,
                kinds=kinds,
                provider_cursor=self._provider_state_value(None, pending_refs),
                resource_ref=resource_ref,
                resource_default=resource_default,
            ),
        }

    def list_roots(self) -> dict[str, Any]:
        """Return configured roots with current metadata in configuration order."""
        items = []
        for root in self._config.roots:
            _configured, item, _path_lower = self._mock_root(root.id)
            items.append(self._result_item(item, root_alias=root.id))
        return {'items': items, 'next_cursor': None}

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one deterministic page of direct currently authorized children."""
        page_size = self._validate_max_results(max_results)
        configured_root, root_item, root_path_lower = self._mock_root(root)
        if folder_ref is not None and (
            not isinstance(folder_ref, str) or not folder_ref or len(folder_ref) > _MAX_ITEM_REF_LENGTH
        ):
            raise DropboxConfigError('folder_ref must be a non-empty string')
        resource_default = folder_ref is None
        resource_ref = configured_root.path if resource_default else folder_ref
        selected = root_item if resource_default else self._find_ref(folder_ref)
        self._authorize(selected, root_path_lower=root_path_lower)
        if selected.get('kind') != 'folder':
            raise DropboxConfigError('selected Dropbox root or item is not a folder')
        selected_path_lower = selected.get('path_lower')
        if not isinstance(selected_path_lower, str):
            raise DropboxConfigError('selected mock Dropbox folder has invalid metadata')
        children = []
        for item in self._items.values():
            candidate = item.get('path_lower')
            if not isinstance(candidate, str) or posixpath.dirname(candidate) != selected_path_lower:
                continue
            self._authorize(item, root_path_lower=root_path_lower)
            children.append(self._result_item(item, root_alias=configured_root.id))
        children.sort(key=lambda item: (_ascii_lower(str(item['path'])), str(item['id'])))
        return self._page(
            children,
            root=configured_root,
            operation='list_folder',
            query=None,
            kinds=(),
            cursor=cursor,
            max_results=page_size,
            root_path_lower=root_path_lower,
            folder_path_lower=selected_path_lower,
            resource_ref=resource_ref,
            resource_default=resource_default,
        )

    def _find_ref(self, item_ref: str | None) -> dict[str, Any]:
        """Resolve a mock provider ID or display path to current metadata."""
        if not isinstance(item_ref, str) or not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH:
            raise DropboxConfigError('Dropbox item reference must be a non-empty string')
        item = self._items.get(item_ref)
        if item is not None:
            return item
        for candidate in self._items.values():
            if candidate.get('path') == item_ref or candidate.get('path_lower') == item_ref:
                return candidate
        raise DropboxNotFoundError('Dropbox item is not seeded')

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch current seeded metadata after authoritative path authorization."""
        configured_root, root_item, root_path_lower = self._mock_root(root)
        if configured_root.path == '/' and item_ref == '/':
            item = root_item
        else:
            item = self._find_ref(item_ref)
        self._authorize(item, root_path_lower=root_path_lower)
        return {'item': self._result_item(item, root_alias=configured_root.id)}

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search names in deterministic provider-path order with current checks."""
        page_size = self._validate_max_results(max_results)
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            raise DropboxConfigError('query must be a string')
        normalized_kinds = self._validate_kinds(kinds)
        configured_root, root_item, root_path_lower = self._mock_root(root)
        if root_item.get('kind') != 'folder':
            raise DropboxConfigError('selected Dropbox root is not a folder')
        matches = []
        for item in self._items.values():
            if _ascii_lower(query) not in _ascii_lower(str(item.get('name', ''))):
                continue
            if normalized_kinds and item.get('kind') not in normalized_kinds:
                continue
            try:
                self._authorize(item, root_path_lower=root_path_lower)
            except DropboxOutsideRootError:
                continue
            matches.append(self._result_item(item, root_alias=configured_root.id))
        matches.sort(key=lambda item: (_ascii_lower(str(item['path'])), str(item['id'])))
        return self._page(
            matches,
            root=configured_root,
            operation='search',
            query=query,
            kinds=normalized_kinds,
            cursor=cursor,
            max_results=page_size,
            root_path_lower=root_path_lower,
        )
