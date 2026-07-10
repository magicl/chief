# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse local agent data files without Django dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from libs.file.hashing import content_hash

_ENVELOPE_FIELDS = ('owner', 'identifier', 'name')


@dataclass(frozen=True)
class AgentDiskFile:
    """Represent one parsed agent file, config body, and disk provenance."""

    owner: str
    identifier: str
    name: str
    body: dict[object, object]
    body_yaml: str
    source_path: str
    source_rev: str


def _non_empty_string(value: Any, *, field: str) -> str:
    """Return a stripped envelope string or reject a missing or blank value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


def parse_agent_file(path: Path, *, root: Path) -> AgentDiskFile:
    """Strip one agent envelope while leaving config validation to app callers."""
    raw = path.read_text(encoding='utf-8')
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise yaml.YAMLError('agent YAML must contain a mapping')

    owner = _non_empty_string(loaded.get('owner'), field='owner')
    identifier = _non_empty_string(loaded.get('identifier', path.stem), field='identifier')
    name = _non_empty_string(loaded.get('name', identifier), field='name')
    body = {key: value for key, value in loaded.items() if key not in _ENVELOPE_FIELDS}
    body_yaml = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    source_path = path.resolve().relative_to(root.resolve()).as_posix()
    # Hash the config body only so envelope-only edits (display name) do not
    # create redundant AgentConfig revisions or rematerialize beat.
    return AgentDiskFile(
        owner=owner,
        identifier=identifier,
        name=name,
        body=body,
        body_yaml=body_yaml,
        source_path=source_path,
        source_rev=content_hash(body_yaml),
    )
