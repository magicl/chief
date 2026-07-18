# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Strict non-secret configuration for Google Drive metadata access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from libs.clients.google_drive.errors import GoogleDriveConfigError

_CONFIG_FIELDS = frozenset({'subject', 'roots'})
_ROOT_FIELDS = frozenset({'id', 'file_id', 'corpus', 'drive_id'})


@dataclass(frozen=True, slots=True)
class GoogleDriveRoot:
    """Identify one aliased Drive file or folder and its search corpus."""

    id: str
    file_id: str
    corpus: Literal['user', 'drive']
    drive_id: str | None = None


@dataclass(frozen=True, slots=True)
class GoogleDriveConfig:
    """Hold delegated identity selection and immutable approved roots."""

    subject: str | None
    roots: tuple[GoogleDriveRoot, ...]


def _optional_nonempty_string(value: Any, *, field: str) -> str | None:
    """Normalize an optional string while rejecting malformed supplied values."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GoogleDriveConfigError(f'{field} must be a string')
    normalized = value.strip()
    return normalized or None


def _required_nonempty_string(value: Any, *, field: str) -> str:
    """Normalize a required non-empty string configuration value."""
    normalized = _optional_nonempty_string(value, field=field)
    if normalized is None:
        raise GoogleDriveConfigError(f'{field} must be a non-empty string')
    return normalized


def _parse_root(raw: Any, *, index: int) -> GoogleDriveRoot:
    """Validate and normalize one configured Drive root."""
    if not isinstance(raw, Mapping):
        raise GoogleDriveConfigError(f'roots[{index}] must be a mapping')
    unknown = set(raw) - _ROOT_FIELDS
    if unknown:
        raise GoogleDriveConfigError(f'roots[{index}] contains unknown fields')

    alias = _required_nonempty_string(raw.get('id'), field=f'roots[{index}].id')
    file_id = _required_nonempty_string(raw.get('file_id'), field=f'roots[{index}].file_id')
    raw_corpus = raw.get('corpus', 'user')
    if raw_corpus not in ('user', 'drive'):
        raise GoogleDriveConfigError(f'roots[{index}].corpus must be user or drive')

    drive_id = _optional_nonempty_string(raw.get('drive_id'), field=f'roots[{index}].drive_id')
    if 'drive_id' in raw and drive_id is None:
        raise GoogleDriveConfigError(f'roots[{index}].drive_id must be a non-empty string')
    if drive_id is not None:
        if 'corpus' in raw and raw_corpus != 'drive':
            raise GoogleDriveConfigError(f'roots[{index}].drive_id requires drive corpus')
        corpus: Literal['user', 'drive'] = 'drive'
    else:
        corpus = cast(Literal['user', 'drive'], raw_corpus)
    if corpus == 'drive' and drive_id is None:
        raise GoogleDriveConfigError(f'roots[{index}].drive corpus requires drive_id')

    return GoogleDriveRoot(id=alias, file_id=file_id, corpus=corpus, drive_id=drive_id)


def parse_google_drive_config(config: Mapping[str, Any]) -> GoogleDriveConfig:
    """Validate non-secret Drive addressing and required aliased roots."""
    if not isinstance(config, Mapping):
        raise GoogleDriveConfigError('Google Drive config must be a mapping')
    unknown = set(config) - _CONFIG_FIELDS
    if unknown:
        raise GoogleDriveConfigError('Google Drive config contains unknown fields')
    raw_roots = config.get('roots')
    if not isinstance(raw_roots, list) or not raw_roots:
        raise GoogleDriveConfigError('config.roots must be a non-empty list')

    roots = tuple(_parse_root(raw, index=index) for index, raw in enumerate(raw_roots))
    aliases = [root.id for root in roots]
    if len(set(aliases)) != len(aliases):
        raise GoogleDriveConfigError('config.roots aliases must be unique')
    file_ids = [root.file_id for root in roots]
    if len(set(file_ids)) != len(file_ids):
        raise GoogleDriveConfigError('config.roots file IDs must be unique')

    subject = _optional_nonempty_string(config.get('subject'), field='config.subject')
    return GoogleDriveConfig(subject=subject, roots=roots)
