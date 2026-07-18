# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Deterministic in-memory Google Drive metadata client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from libs.clients.google_drive.client import (
    _FOLDER_MIME_TYPE,
    _MAX_ANCESTRY_DEPTH,
    _MAX_ITEM_REF_LENGTH,
    _MAX_QUERY_LENGTH,
    _SHORTCUT_MIME_TYPE,
    GoogleDriveClient,
)
from libs.clients.google_drive.config import GoogleDriveRoot
from libs.clients.google_drive.errors import (
    GoogleDriveConfigError,
    GoogleDriveNotFoundError,
    GoogleDriveOutsideRootError,
)


class MockGoogleDriveClient(GoogleDriveClient):
    """Model root-safe Drive metadata behavior without provider or content APIs."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
    ) -> None:
        """Create an in-memory metadata tree with the production constructor shape."""
        super().__init__(
            token_supplier=token_supplier,
            config=config,
            instance_id=instance_id,
        )
        self._items: dict[str, dict[str, Any]] = {}
        self._canonical_roots: dict[str, str] = {}

    def seed_item(
        self,
        item_id: str,
        *,
        name: str,
        kind: Literal['file', 'folder', 'shortcut'],
        parent_refs: tuple[str, ...] = (),
        drive_id: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Add or replace metadata so tests can model moves and cycles."""
        if kind == 'folder':
            mime_type = _FOLDER_MIME_TYPE
        elif kind == 'shortcut':
            mime_type = _SHORTCUT_MIME_TYPE
        else:
            mime_type = 'application/octet-stream'
        item = {
            'provider': 'google_drive',
            'root': None,
            'id': item_id,
            'name': name,
            'kind': kind,
            'mime_type': mime_type,
            'size': None,
            'modified_at': None,
            'parent_refs': list(parent_refs),
            'path': path,
            'web_url': None,
            'provider_metadata': {'drive_id': drive_id} if drive_id else {},
        }
        self._items[item_id] = item
        return dict(item)

    def seed_root(self, root_alias: str, *, canonical_item_id: str) -> None:
        """Map Drive's special root locator to a distinct canonical seeded item."""
        configured_root = self._root(root_alias)
        if canonical_item_id not in self._items:
            raise GoogleDriveConfigError('canonical mock Google Drive root must be seeded first')
        if configured_root.file_id != 'root' or canonical_item_id == 'root':
            raise GoogleDriveConfigError('canonical mock root mapping requires a distinct special root locator')
        self._canonical_roots[root_alias] = canonical_item_id

    def _mock_root(self, alias: str) -> tuple[GoogleDriveRoot, dict[str, Any]]:
        """Resolve one configured alias to current seeded root metadata."""
        configured_root = self._root(alias)
        canonical_id = self._canonical_roots.get(alias, configured_root.file_id)
        item = self._items.get(canonical_id)
        if item is None:
            raise GoogleDriveNotFoundError('configured Google Drive root is not seeded')
        return configured_root, item

    def _authorize(self, item: Mapping[str, Any], *, canonical_root_id: str) -> None:
        """Walk current seeded parents with production-equivalent cycle and depth bounds."""
        if item.get('id') == canonical_root_id:
            return
        item_id = item.get('id')
        if not isinstance(item_id, str):
            raise GoogleDriveOutsideRootError('Google Drive item is outside the configured root')
        raw_parents = item.get('parent_refs')
        parents = [parent for parent in raw_parents if isinstance(parent, str)] if isinstance(raw_parents, list) else []
        pending = [(parent, 1) for parent in parents]
        visited = {item_id}
        while pending:
            parent_id, depth = pending.pop()
            if depth > _MAX_ANCESTRY_DEPTH or parent_id in visited:
                continue
            if parent_id == canonical_root_id:
                return
            visited.add(parent_id)
            parent = self._items.get(parent_id)
            if parent is None:
                continue
            parent_refs = parent.get('parent_refs')
            if isinstance(parent_refs, list):
                pending.extend((next_parent, depth + 1) for next_parent in parent_refs if isinstance(next_parent, str))
        raise GoogleDriveOutsideRootError('Google Drive item is outside the configured root')

    def _result_item(self, item: Mapping[str, Any], *, root_alias: str) -> dict[str, Any]:
        """Copy one seeded record and bind it to the selected root alias."""
        result = dict(item)
        result['root'] = root_alias
        result['parent_refs'] = list(item.get('parent_refs', []))
        result['provider_metadata'] = dict(item.get('provider_metadata', {}))
        return result

    def _page(
        self,
        items: list[dict[str, Any]],
        *,
        configured_root: GoogleDriveRoot,
        operation: str,
        query: str | None,
        kinds: tuple[str, ...],
        cursor: str | None,
        max_results: int,
        resource_ref: str | None = None,
        resource_default: bool = False,
    ) -> dict[str, Any]:
        """Paginate deterministic search results with operation-bound offsets."""
        provider_cursor = self._decode_cursor(
            cursor,
            root=configured_root,
            operation=operation,
            query=query,
            kinds=kinds,
            resource_ref=resource_ref,
            resource_default=resource_default,
        )
        offset = 0
        if provider_cursor is not None:
            try:
                offset = int(provider_cursor)
            except ValueError:
                raise GoogleDriveConfigError('mock Google Drive cursor offset is invalid') from None
            if offset < 0:
                raise GoogleDriveConfigError('mock Google Drive cursor offset is invalid')
        page_items = items[offset : offset + max_results]
        next_offset = offset + len(page_items)
        next_provider_cursor = str(next_offset) if next_offset < len(items) else None
        return {
            'items': page_items,
            'next_cursor': self._encode_cursor(
                root=configured_root,
                operation=operation,
                query=query,
                kinds=kinds,
                provider_cursor=next_provider_cursor,
                resource_ref=resource_ref,
                resource_default=resource_default,
            ),
        }

    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured roots only in alias order."""
        items = []
        for configured_root in sorted(self._config.roots, key=lambda root: root.id.casefold()):
            canonical_id = self._canonical_roots.get(configured_root.id, configured_root.file_id)
            item = self._items.get(canonical_id)
            if item is None:
                raise GoogleDriveNotFoundError('configured Google Drive root is not seeded')
            items.append(self._result_item(item, root_alias=configured_root.id))
        return {'items': items, 'next_cursor': None}

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one deterministic page of direct authorized children."""
        page_size = self._validate_max_results(max_results)
        configured_root, root_item = self._mock_root(root)
        if folder_ref is not None and (
            not isinstance(folder_ref, str) or not folder_ref or len(folder_ref) > _MAX_ITEM_REF_LENGTH
        ):
            raise GoogleDriveConfigError('folder_ref must be a non-empty string')
        folder_default = folder_ref is None
        selected_ref = root_item.get('id') if folder_default else folder_ref
        if not isinstance(selected_ref, str):
            raise GoogleDriveConfigError('mock Google Drive folder ID is invalid')
        selected = self._items.get(selected_ref)
        if selected is None:
            raise GoogleDriveNotFoundError('Google Drive folder is not seeded')
        self._authorize(selected, canonical_root_id=root_item['id'])
        if selected.get('kind') != 'folder':
            raise GoogleDriveConfigError('selected Google Drive root or item is not a folder')
        encoded_state = self._decode_cursor(
            cursor,
            root=configured_root,
            operation='list_folder',
            query=None,
            kinds=(),
            resource_ref=selected_ref,
            resource_default=folder_default,
        )
        _provider_cursor, pending_refs = self._provider_state(encoded_state)
        if cursor is None:
            children = []
            for item in self._items.values():
                if selected_ref not in item.get('parent_refs', []):
                    continue
                children.append(item)
            children.sort(
                key=lambda item: (
                    item['kind'] != 'folder',
                    str(item['name']).casefold(),
                    str(item['id']),
                )
            )
            pending_refs = [str(item['id']) for item in children]

        page_items: list[dict[str, Any]] = []
        while pending_refs and len(page_items) < page_size:
            item_id = pending_refs.pop(0)
            buffered_item = self._items.get(item_id)
            if buffered_item is None:
                raise GoogleDriveNotFoundError('Google Drive buffered item is not seeded')
            self._authorize(buffered_item, canonical_root_id=root_item['id'])
            parents = buffered_item.get('parent_refs')
            if not isinstance(parents, list) or selected_ref not in parents:
                raise GoogleDriveOutsideRootError(
                    'Google Drive item is no longer a direct child of the selected folder'
                )
            page_items.append(self._result_item(buffered_item, root_alias=configured_root.id))

        return {
            'items': page_items,
            'next_cursor': self._encode_cursor(
                root=configured_root,
                operation='list_folder',
                query=None,
                kinds=(),
                provider_cursor=self._provider_state_value(None, pending_refs),
                resource_ref=selected_ref,
                resource_default=folder_default,
            ),
        }

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch seeded metadata after checking its current ancestry."""
        if not isinstance(item_ref, str) or not item_ref or len(item_ref) > _MAX_ITEM_REF_LENGTH:
            raise GoogleDriveConfigError('item_ref must be a non-empty string')
        configured_root, root_item = self._mock_root(root)
        item = self._items.get(item_ref)
        if item is None:
            raise GoogleDriveNotFoundError('Google Drive item is not seeded')
        self._authorize(item, canonical_root_id=root_item['id'])
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
        """Search seeded names and retain only currently authorized metadata."""
        page_size = self._validate_max_results(max_results)
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            raise GoogleDriveConfigError('query must be a string')
        normalized_kinds = self._validate_kinds(kinds)
        configured_root, root_item = self._mock_root(root)
        if root_item.get('kind') != 'folder':
            raise GoogleDriveConfigError('selected Google Drive root is not a folder')
        matches = []
        for item in self._items.values():
            if query.casefold() not in str(item.get('name', '')).casefold():
                continue
            kind = item.get('kind')
            if normalized_kinds and kind not in normalized_kinds:
                continue
            if normalized_kinds == ('file',) and kind == 'shortcut':
                continue
            try:
                self._authorize(item, canonical_root_id=root_item['id'])
            except GoogleDriveOutsideRootError:
                continue
            matches.append(self._result_item(item, root_alias=configured_root.id))
        matches.sort(key=lambda item: (str(item['name']).casefold(), str(item['id'])))
        return self._page(
            matches,
            configured_root=configured_root,
            operation='search',
            query=query,
            kinds=normalized_kinds,
            cursor=cursor,
            max_results=page_size,
        )
