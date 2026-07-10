# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Save metadata for agent config (DB-only)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from apps.agents.models import Agent, AgentConfigSource


def normalize_spec_bytes(raw: str) -> bytes:
    """Normalize YAML text for stable content hashing."""
    return raw.replace('\r\n', '\n').encode('utf-8')


def spec_content_hash(raw: str) -> str:
    """Return ``sha256:<hex>`` digest for normalized spec bytes."""
    digest = hashlib.sha256(normalize_spec_bytes(raw)).hexdigest()
    return f'sha256:{digest}'


def compute_save_metadata(_agent: Agent, _raw_yaml: str) -> tuple[str, bool]:
    """Compute ``source_rev`` and ``dirty`` for a UI save (DB is source of truth)."""
    ts = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    return f'ui:{ts}', False


def config_source_label(config_source: str) -> str:
    """Human-readable label for the config source badge."""
    if config_source == AgentConfigSource.UI:
        return 'UI'
    if config_source == AgentConfigSource.DISK:
        return 'Disk'
    return 'Legacy'
