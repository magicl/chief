# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Finite cross-domain reconciliation for configured local providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from apps.agents.services.disk_sync import sync_agents_dir
from apps.keys.services.disk_sync import sync_keys_dir
from django.conf import settings
from libs.file.sync import SyncProgressCallback, SyncReport


@dataclass(frozen=True)
class LocalSyncReport:
    """Return separate domain reports while preserving execution order."""

    keys: SyncReport
    agents: SyncReport


def resolve_local_root() -> Path | None:
    """Resolve CHIEF_LOCAL_DIR without creating operator-owned paths."""
    raw_root = str(getattr(settings, 'CHIEF_LOCAL_DIR', '') or '').strip()
    if not raw_root:
        return None
    return Path(raw_root).expanduser().resolve()


def reconcile_local_providers(
    *,
    root: Path,
    progress: SyncProgressCallback | None = None,
) -> LocalSyncReport:
    """Run one finite keys-before-agents reconciliation."""
    # Agent materialization may resolve credentials, so this ordering is an invariant.
    key_report = sync_keys_dir(root=root, progress=progress)
    agent_report = sync_agents_dir(root=root, progress=progress)
    return LocalSyncReport(keys=key_report, agents=agent_report)
