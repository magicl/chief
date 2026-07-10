# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse local credential YAML files into validated disk records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from apps.keys.exceptions import KeyValidationError
from apps.keys.types import validate_type

from .hashing import content_hash


@dataclass(frozen=True)
class KeyDiskFile:
    """Represent one parsed credential file and its disk provenance."""

    name: str
    type: str
    owner: str
    value: str
    source_path: str
    source_rev: str


def parse_key_file(path: Path, *, root: Path) -> KeyDiskFile:
    """Parse and validate one credential YAML file under ``root``."""
    raw = path.read_text(encoding='utf-8')
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise yaml.YAMLError('credential YAML must contain a mapping')

    fields: dict[str, str] = {}
    for field in ('type', 'owner', 'value'):
        value = loaded.get(field)
        if not isinstance(value, str) or not value.strip():
            raise KeyValidationError(f'{field} must be a non-empty string')
        fields[field] = value

    raw_name = loaded.get('name', path.stem)
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise KeyValidationError('name must be a non-empty string')

    type_name = validate_type(fields['type'].strip())
    source_path = path.resolve().relative_to(root.resolve()).as_posix()
    return KeyDiskFile(
        name=raw_name.strip(),
        type=type_name,
        owner=fields['owner'].strip(),
        value=fields['value'],
        source_path=source_path,
        source_rev=content_hash(raw),
    )
