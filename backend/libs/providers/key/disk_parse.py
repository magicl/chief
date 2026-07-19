# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse local credential YAML files without Django dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from libs.file.hashing import content_hash
from yaml.nodes import MappingNode


class _StrictSafeLoader(yaml.SafeLoader):
    """Load safe YAML while refusing ambiguous mapping construction."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        """Reject duplicate and merge keys before constructing mapping values."""
        seen: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                raise yaml.YAMLError('credential YAML merge keys are not supported')
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.YAMLError('credential YAML contains a duplicate mapping key')
                seen.add(key)
            except TypeError:
                raise yaml.YAMLError('credential YAML mapping keys must be scalar') from None
        return super().construct_mapping(node, deep=deep)


@dataclass(frozen=True)
class KeyDiskFile:
    """Represent one parsed credential file and its disk provenance."""

    name: str
    type: str
    owner: str
    auth_kind: Literal['static', 'oauth']
    value: str | None
    capabilities: tuple[str, ...]
    source_path: str
    source_rev: str


def _required_string(loaded: dict[object, object], field: str) -> str:
    """Return one required non-empty string field from parsed YAML."""
    value = loaded.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value


def _parse_auth(loaded: dict[object, object]) -> tuple[Literal['static', 'oauth'], str | None, tuple[str, ...]]:
    """Parse exactly one static or OAuth declaration without exposing field values."""
    common_fields = {'name', 'type', 'owner'}
    if 'source' in loaded:
        if loaded.get('source') != 'oauth':
            raise ValueError('source must be oauth')
        if set(loaded) - common_fields != {'source', 'scopes'}:
            raise ValueError('OAuth credential fields are invalid')
        raw_scopes = loaded.get('scopes')
        if (
            not isinstance(raw_scopes, list)
            or not raw_scopes
            or any(not isinstance(scope, str) or not scope.strip() for scope in raw_scopes)
        ):
            raise ValueError('scopes must be a non-empty list of capability identifiers')
        return 'oauth', None, tuple(raw_scopes)

    if set(loaded) - common_fields != {'value'}:
        raise ValueError('static credential fields are invalid')
    raw_value = loaded['value']
    if raw_value is None:
        value = ''
    elif isinstance(raw_value, str):
        value = raw_value
    else:
        raise ValueError('value must be a string')
    return 'static', value, ()


def _strict_safe_load(raw: str) -> object:
    """Load YAML with strict mapping rules and scrub source text from failures."""
    loader: _StrictSafeLoader | None = None
    failed = False
    loaded: object = None
    try:
        loader = _StrictSafeLoader(raw)
        loaded = loader.get_single_data()
    except yaml.YAMLError:
        failed = True
    finally:
        if loader is not None:
            loader.dispose()  # type: ignore[no-untyped-call]
        loader = None
        raw = ''
    if failed:
        raise yaml.YAMLError('credential YAML could not be parsed') from None
    return loaded


def parse_key_file(path: Path, *, root: Path) -> KeyDiskFile:
    """Parse one credential YAML file while leaving type validation to callers."""
    raw = path.read_text(encoding='utf-8')
    source_rev = content_hash(raw)
    failure: yaml.YAMLError | None = None
    loaded: object = None
    try:
        loaded = _strict_safe_load(raw)
    except yaml.YAMLError as exc:
        failure = exc.with_traceback(None)
    finally:
        raw = ''
    if failure is not None:
        raise failure.with_traceback(None) from None
    if not isinstance(loaded, dict):
        raise yaml.YAMLError('credential YAML must contain a mapping')

    type_name = _required_string(loaded, 'type').strip()
    owner = _required_string(loaded, 'owner').strip()
    auth_kind, value, capabilities = _parse_auth(loaded)
    raw_name = loaded.get('name', path.stem)
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError('name must be a non-empty string')

    source_path = path.resolve().relative_to(root.resolve()).as_posix()
    return KeyDiskFile(
        name=raw_name.strip(),
        type=type_name,
        owner=owner,
        auth_kind=auth_kind,
        value=value,
        capabilities=capabilities,
        source_path=source_path,
        source_rev=source_rev,
    )
