# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Synchronize local credential YAML files into encrypted database rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from apps.keys.exceptions import KeyValidationError
from apps.keys.models import CredentialSource, CredentialStatus, UserCredential
from apps.keys.services.commands import upsert_user_named_from_disk

from .key_parse import parse_key_file
from .owner import resolve_owner
from .paths import resolve_local_root

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncItemResult:
    """Describe whether one local file synchronized successfully."""

    source_path: str
    success: bool
    detail: str = ''


@dataclass
class SyncReport:
    """Collect per-file synchronization outcomes and soft-disable count."""

    items: list[SyncItemResult] = field(default_factory=list)
    disabled: int = 0

    @property
    def succeeded(self) -> int:
        """Return the number of successfully synchronized files."""
        return sum(item.success for item in self.items)

    @property
    def failed(self) -> int:
        """Return the number of files that could not be synchronized."""
        return sum(not item.success for item in self.items)


def _relative_path(path: Path, root: Path) -> str:
    """Return a safe root-relative path for reports and logs."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def sync_key_path(path: Path, *, root: Path) -> SyncItemResult:
    """Parse and synchronize one credential file while containing file-level failures."""
    source_path = _relative_path(path, root)
    try:
        parsed = parse_key_file(path, root=root)
        owner = resolve_owner(parsed.owner)
        if owner is None:
            logger.error('Credential owner not found for %s (owner=%s)', source_path, parsed.owner)
            return SyncItemResult(source_path=source_path, success=False, detail='owner not found')
        upsert_user_named_from_disk(
            owner.pk,
            parsed.name,
            parsed.type,
            parsed.value,
            source_path=parsed.source_path,
            source_rev=parsed.source_rev,
        )
    except (OSError, UnicodeError, yaml.YAMLError, KeyValidationError, ValueError) as exc:
        # Exception messages from YAML parsers can quote source lines, including values.
        logger.error('Credential file sync failed for %s (%s)', source_path, type(exc).__name__)
        return SyncItemResult(source_path=source_path, success=False, detail=type(exc).__name__)
    return SyncItemResult(source_path=source_path, success=True)


def soft_disable_missing_disk_keys(*, present_paths: set[str]) -> int:
    """Disable active disk credentials whose bound files are no longer present."""
    missing = UserCredential.objects.filter(
        source=CredentialSource.DISK,
        status=CredentialStatus.ACTIVE,
    ).exclude(source_path__in=present_paths)
    return missing.update(status=CredentialStatus.DISABLED)


def sync_keys_dir() -> SyncReport:
    """Synchronize all key YAML files under the configured local root."""
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return SyncReport()

    directory = root / 'keys'
    paths: set[Path] = set()
    if directory.is_dir():
        paths.update(directory.glob('*.yaml'))
        paths.update(directory.glob('*.yml'))

    present_paths = {_relative_path(path, root) for path in paths}
    report = SyncReport(items=[sync_key_path(path, root=root) for path in sorted(paths)])
    report.disabled = soft_disable_missing_disk_keys(present_paths=present_paths)
    return report
