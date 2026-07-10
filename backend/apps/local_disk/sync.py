# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Coordinate complete synchronization of local disk providers."""

from __future__ import annotations

from .agent_sync import sync_agents_dir
from .key_sync import SyncReport, sync_keys_dir
from .paths import resolve_local_root


def sync_all() -> SyncReport:
    """Ingest keys before agents when the configured local root exists."""
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return SyncReport()

    # Agent materialization may reference credentials, so preserve this order.
    (root / 'keys').mkdir(exist_ok=True)
    (root / 'agents').mkdir(exist_ok=True)
    key_report = sync_keys_dir()
    agent_report = sync_agents_dir()
    return SyncReport(
        items=[*key_report.items, *agent_report.items],
        disabled=key_report.disabled + agent_report.disabled,
    )
