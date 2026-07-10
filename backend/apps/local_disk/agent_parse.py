# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse local agent YAML envelopes into validated config specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from apps.agents.services.config_validation import validate_agent_config_yaml
from libs.agent_spec import AgentConfigSpec

from .hashing import content_hash

_ENVELOPE_FIELDS = ('owner', 'identifier', 'name')


@dataclass(frozen=True)
class AgentDiskFile:
    """Represent one parsed agent file and its disk provenance."""

    owner: str
    identifier: str
    name: str
    spec: AgentConfigSpec
    body_yaml: str
    source_path: str
    source_rev: str


def _non_empty_string(value: Any, *, field: str) -> str:
    """Return a stripped envelope string or reject a missing/blank value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


def parse_agent_file(path: Path, *, root: Path) -> AgentDiskFile:
    """Strip one disk envelope and validate its remaining agent config body."""
    raw = path.read_text(encoding='utf-8')
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise yaml.YAMLError('agent YAML must contain a mapping')

    owner = _non_empty_string(loaded.get('owner'), field='owner')
    identifier = _non_empty_string(loaded.get('identifier', path.stem), field='identifier')
    name = _non_empty_string(loaded.get('name', identifier), field='name')
    body = {key: value for key, value in loaded.items() if key not in _ENVELOPE_FIELDS}
    body_yaml = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    spec = validate_agent_config_yaml(body_yaml)
    source_path = path.resolve().relative_to(root.resolve()).as_posix()
    return AgentDiskFile(
        owner=owner,
        identifier=identifier,
        name=name,
        spec=spec,
        body_yaml=body_yaml,
        source_path=source_path,
        source_rev=content_hash(raw),
    )
