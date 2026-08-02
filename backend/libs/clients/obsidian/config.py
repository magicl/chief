# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Strict non-secret configuration for the Obsidian tool instance.

Parses the tool's `vault` (remote Sync vault id known to the vault service)
and `roots` (allowed path prefixes, enforced again server-side by the vault
service — this parse only validates shape before the client ever sends a
request).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from libs.clients.obsidian.errors import ObsidianConfigError

_CONFIG_FIELDS = frozenset({'vault', 'roots'})


@dataclass(frozen=True, slots=True)
class ObsidianToolConfig:
    """Hold the validated vault identity and allowed path roots for one tool instance."""

    vault: str
    roots: tuple[str, ...]


def _required_nonempty_string(value: Any, *, field: str) -> str:
    """Normalize a required string while rejecting empty or malformed values."""
    if not isinstance(value, str) or not value.strip():
        raise ObsidianConfigError(f'{field} must be a non-empty string')
    return value.strip()


def parse_obsidian_tool_config(config: Mapping[str, Any]) -> ObsidianToolConfig:
    """Validate the vault id and non-empty root list for one Obsidian tool instance."""
    if not isinstance(config, Mapping):
        raise ObsidianConfigError('Obsidian tool config must be a mapping')
    unknown = set(config) - _CONFIG_FIELDS
    if unknown:
        raise ObsidianConfigError('Obsidian tool config contains unknown fields')

    vault = _required_nonempty_string(config.get('vault'), field='vault')

    raw_roots = config.get('roots')
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ObsidianConfigError('roots must be a non-empty list')
    roots = tuple(
        _required_nonempty_string(raw_root, field=f'roots[{index}]') for index, raw_root in enumerate(raw_roots)
    )
    return ObsidianToolConfig(vault=vault, roots=roots)
