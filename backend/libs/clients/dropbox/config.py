# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Strict non-secret configuration for Dropbox metadata access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from libs.clients.dropbox.errors import DropboxConfigError

_CONFIG_FIELDS = frozenset({'namespace_id', 'roots'})
_ROOT_FIELDS = frozenset({'id', 'path'})
_ASCII_LOWER_TABLE = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


@dataclass(frozen=True, slots=True)
class DropboxRoot:
    """Identify one aliased absolute Dropbox root path."""

    id: str
    path: str


@dataclass(frozen=True, slots=True)
class DropboxConfig:
    """Hold optional namespace selection and immutable approved roots."""

    namespace_id: str | None
    roots: tuple[DropboxRoot, ...]


def _required_nonempty_string(value: Any, *, field: str) -> str:
    """Normalize a required string while rejecting empty or malformed values."""
    if not isinstance(value, str) or not value.strip():
        raise DropboxConfigError(f'{field} must be a non-empty string')
    return value.strip()


def normalize_dropbox_path(path: str) -> str:
    """Return one absolute normalized Dropbox path, preserving '/' as root."""
    if not isinstance(path, str) or not path or not path.startswith('/'):
        raise DropboxConfigError('Dropbox path must be a non-empty absolute path')
    if path == '/':
        return path
    if path.endswith('/'):
        raise DropboxConfigError('Dropbox path must not have a trailing separator')
    parts = path[1:].split('/')
    if any(not part or part in {'.', '..'} for part in parts):
        raise DropboxConfigError('Dropbox path must contain normalized path segments')
    return '/' + '/'.join(parts)


def _ascii_lower(value: str) -> str:
    """Fold ASCII casing without changing provider-sensitive Unicode code points."""
    return value.translate(_ASCII_LOWER_TABLE)


def _path_lower_parts(path_lower: str) -> tuple[str, ...]:
    """Split an authoritative Dropbox path_lower without Unicode renormalization."""
    validated = normalize_dropbox_path(path_lower)
    if validated == '/':
        return ()
    return tuple(validated[1:].split('/'))


def is_path_within(root_path_lower: str, candidate_path_lower: str) -> bool:
    """Compare segments; callers must pass authoritative Dropbox path_lower strings."""
    root_parts = _path_lower_parts(root_path_lower)
    candidate_parts = _path_lower_parts(candidate_path_lower)
    return candidate_parts[: len(root_parts)] == root_parts


def _parse_root(raw: Any, *, index: int) -> DropboxRoot:
    """Validate and normalize one configured Dropbox root."""
    if not isinstance(raw, Mapping):
        raise DropboxConfigError(f'roots[{index}] must be a mapping')
    if set(raw) - _ROOT_FIELDS:
        raise DropboxConfigError(f'roots[{index}] contains unknown fields')
    alias = _required_nonempty_string(raw.get('id'), field=f'roots[{index}].id')
    raw_path = raw.get('path')
    if not isinstance(raw_path, str):
        raise DropboxConfigError(f'roots[{index}].path must be a string')
    path = normalize_dropbox_path(raw_path)
    return DropboxRoot(id=alias, path=path)


def parse_dropbox_config(config: Mapping[str, Any]) -> DropboxConfig:
    """Validate namespace selection and required aliased absolute roots."""
    if not isinstance(config, Mapping):
        raise DropboxConfigError('Dropbox config must be a mapping')
    if set(config) - _CONFIG_FIELDS:
        raise DropboxConfigError('Dropbox config contains unknown fields')
    raw_roots = config.get('roots')
    if not isinstance(raw_roots, list) or not raw_roots:
        raise DropboxConfigError('config.roots must be a non-empty list')

    roots = tuple(_parse_root(raw, index=index) for index, raw in enumerate(raw_roots))
    aliases = [root.id for root in roots]
    if len(set(aliases)) != len(aliases):
        raise DropboxConfigError('config.roots aliases must be unique')
    lowered_paths = [_ascii_lower(root.path) for root in roots]
    if len(set(lowered_paths)) != len(lowered_paths):
        raise DropboxConfigError('config.roots paths must be unique')

    namespace_id = None
    if 'namespace_id' in config:
        namespace_id = _required_nonempty_string(config.get('namespace_id'), field='config.namespace_id')
    return DropboxConfig(namespace_id=namespace_id, roots=roots)
