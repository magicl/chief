# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse local credential YAML files without Django dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from libs.file.hashing import content_hash


@dataclass(frozen=True)
class KeyDiskFile:
    """Represent one parsed credential file and its disk provenance."""

    name: str
    type: str
    owner: str
    value: str
    source_path: str
    source_rev: str


def _required_string(loaded: dict[object, object], field: str) -> str:
    """Return one required non-empty string field from parsed YAML."""
    value = loaded.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value


def parse_key_file(path: Path, *, root: Path) -> KeyDiskFile:
    """Parse one credential YAML file while leaving type validation to callers."""
    raw = path.read_text(encoding='utf-8')
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise yaml.YAMLError('credential YAML must contain a mapping')

    type_name = _required_string(loaded, 'type').strip()
    owner = _required_string(loaded, 'owner').strip()
    if 'value' not in loaded:
        raise ValueError('value is required')
    raw_value = loaded['value']
    if raw_value is None:
        value = ''
    elif isinstance(raw_value, str):
        value = raw_value
    else:
        raise ValueError('value must be a string')
    raw_name = loaded.get('name', path.stem)
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError('name must be a non-empty string')

    source_path = path.resolve().relative_to(root.resolve()).as_posix()
    return KeyDiskFile(
        name=raw_name.strip(),
        type=type_name,
        owner=owner,
        value=value,
        source_path=source_path,
        source_rev=content_hash(raw),
    )
