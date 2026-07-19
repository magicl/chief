# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Synchronize local credential YAML files into encrypted database rows."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from apps.bus.resources import publish_resource_update_after_commit
from apps.keys.exceptions import KeyValidationError
from apps.keys.models import CredentialSource, CredentialStatus, UserCredential
from apps.keys.oauth.services import normalize_auth_config
from apps.keys.services.commands import upsert_user_named_from_disk
from apps.keys.services.owner import resolve_owner
from apps.keys.types import validate_type
from django.conf import settings
from django.db import IntegrityError, transaction
from libs.file.sync import SyncItemResult, SyncProgressCallback, SyncReport
from libs.providers.key.disk_parse import parse_key_file

logger = logging.getLogger(__name__)


def _configured_root() -> Path | None:
    """Return the configured absolute local root, or none when disabled."""
    raw = str(getattr(settings, 'CHIEF_LOCAL_DIR', '') or '').strip()
    return Path(raw).expanduser().resolve() if raw else None


def _relative_path(path: Path, root: Path) -> str:
    """Return a safe root-relative path for reports and logs."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def sync_key_path(
    path: Path,
    *,
    root: Path,
    seen_identities: set[tuple[int, str]] | None = None,
) -> SyncItemResult:
    """Parse and synchronize one credential file while containing file-level failures."""
    source_path = _relative_path(path, root)
    try:
        parsed = parse_key_file(path, root=root)
        type_name = validate_type(parsed.type)
        auth_config: dict[str, object] = {}
        if parsed.auth_kind == 'oauth':
            auth_config = normalize_auth_config(
                provider_id='google',
                credential_type=type_name,
                capability_ids=parsed.capabilities,
            )
        owner = resolve_owner(parsed.owner)
        if owner is None:
            logger.error('Credential owner not found for %s (owner=%s)', source_path, parsed.owner)
            return SyncItemResult(source_path=source_path, success=False, detail='owner not found')
        identity = (int(owner.pk), parsed.name)
        if seen_identities is not None and identity in seen_identities:
            logger.error(
                'Duplicate credential identity for %s (owner=%s name=%s)',
                source_path,
                parsed.owner,
                parsed.name,
            )
            return SyncItemResult(source_path=source_path, success=False, detail='duplicate identity')
        _, changed = upsert_user_named_from_disk(
            owner.pk,
            parsed.name,
            type_name,
            parsed.value,
            auth_kind=parsed.auth_kind,
            auth_config=auth_config,
            source_path=parsed.source_path,
            source_rev=parsed.source_rev,
        )
        if seen_identities is not None:
            seen_identities.add(identity)
    except KeyValidationError as exc:
        logger.error('Credential file validation failed for %s (%s)', source_path, type(exc).__name__)
        return SyncItemResult(source_path=source_path, success=False, detail=str(exc))
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, IntegrityError) as exc:
        # YAML parser messages can quote source lines, including credential values.
        logger.error('Credential file sync failed for %s (%s)', source_path, type(exc).__name__)
        return SyncItemResult(source_path=source_path, success=False, detail=type(exc).__name__)
    return SyncItemResult(source_path=source_path, success=True, user_id=owner.pk, changed=changed)


@transaction.atomic
def soft_disable_missing_disk_keys(*, present_paths: set[str]) -> tuple[int, set[int]]:
    """Disable absent disk keys and return row count plus distinct owners."""
    missing = UserCredential.objects.filter(
        source=CredentialSource.DISK,
        status=CredentialStatus.ACTIVE,
    ).exclude(source_path__in=present_paths)
    missing_rows = list(missing.select_for_update().values_list('pk', 'user_id'))
    if not missing_rows:
        return 0, set()

    missing_pks = [pk for pk, _ in missing_rows]
    user_ids = {user_id for _, user_id in missing_rows}
    UserCredential.objects.filter(pk__in=missing_pks).update(status=CredentialStatus.DISABLED)
    for user_id in sorted(user_ids):
        publish_resource_update_after_commit(user_id, 'keys')
    return len(missing_rows), user_ids


def sync_keys_dir(
    *,
    root: Path | None = None,
    progress: SyncProgressCallback | None = None,
) -> SyncReport:
    """Synchronize key files with optional generic progress checkpoints."""
    resolved_root = root if root is not None else _configured_root()
    if resolved_root is None or not resolved_root.is_dir():
        return SyncReport()

    directory = resolved_root / 'keys'
    paths: set[Path] = set()
    if directory.is_dir():
        paths.update(directory.glob('*.yaml'))
        paths.update(directory.glob('*.yml'))

    present_paths = {_relative_path(path, resolved_root) for path in paths}
    seen_identities: set[tuple[int, str]] = set()
    report = SyncReport()
    for path in sorted(paths):
        if progress is not None:
            progress()
        report.items.append(sync_key_path(path, root=resolved_root, seen_identities=seen_identities))
        if progress is not None:
            progress()
    if progress is not None:
        progress()
    disabled_count, disabled_user_ids = soft_disable_missing_disk_keys(present_paths=present_paths)
    report.disabled = disabled_count
    report.disabled_user_ids = disabled_user_ids
    return report
