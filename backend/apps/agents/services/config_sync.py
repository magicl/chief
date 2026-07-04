# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""File hash and save metadata for agent config sync."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from apps.agents.models import Agent


def normalize_spec_bytes(raw: str) -> bytes:
    """Normalize YAML text for stable content hashing."""
    return raw.replace('\r\n', '\n').encode('utf-8')


def spec_content_hash(raw: str) -> str:
    """Return ``sha256:<hex>`` digest for normalized spec bytes."""
    digest = hashlib.sha256(normalize_spec_bytes(raw)).hexdigest()
    return f'sha256:{digest}'


def read_file_spec_text(path: str) -> str:
    """Read a config file and normalize line endings."""
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    return text.replace('\r\n', '\n')


def file_path_from_source(config_source: str) -> str | None:
    """Extract absolute path from ``file:`` config source, if any."""
    if not config_source.startswith('file:'):
        return None
    return config_source[5:]


def compute_save_metadata(agent: Agent, raw_yaml: str) -> tuple[str, bool]:
    """Compute ``source_rev`` and ``dirty`` for a UI save."""
    file_path = file_path_from_source(agent.config_source)
    if file_path is None:
        ts = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        return f'ui:{ts}', False
    file_hash = spec_content_hash(read_file_spec_text(file_path))
    save_hash = spec_content_hash(raw_yaml)
    dirty = file_hash != save_hash
    return f'ui-save:{save_hash}', dirty


def config_source_label(config_source: str) -> str:
    """Human-readable label for the config source badge."""
    if config_source.startswith('file:'):
        return 'File'
    if config_source == 'ui':
        return 'UI'
    return 'Legacy'
