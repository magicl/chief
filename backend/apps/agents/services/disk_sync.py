# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Synchronize local agent data files into database-backed revisions."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from apps.agents.ingest import IngestError, create_agent_from_spec, persist_agent_config
from apps.agents.models import Agent, AgentConfigSource, AgentStatus
from apps.agents.services.config_validation import (
    ConfigValidationError,
    validate_agent_config_yaml,
)
from apps.agents.services.schedule_beat import sync_agent_schedule_triggers
from apps.bus.resources import publish_resource_update_after_commit
from apps.keys.services.owner import resolve_owner
from django.conf import settings
from django.db import IntegrityError, transaction
from libs.file.sync import SyncItemResult, SyncProgressCallback, SyncReport
from libs.providers.data.agent_disk_parse import AgentDiskFile, parse_agent_file

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


@transaction.atomic
def _persist_parsed_agent(parsed: AgentDiskFile) -> tuple[int, bool]:
    """Persist one disk agent and return its owner plus visible mutation state."""
    spec = validate_agent_config_yaml(parsed.body_yaml)
    owner = resolve_owner(parsed.owner)
    if owner is None:
        raise ValueError('owner not found')

    agent = Agent.objects.select_for_update().filter(user_id=owner.pk, identifier=parsed.identifier).first()
    if agent is None:
        create_agent_from_spec(
            owner,
            spec,
            name=parsed.name,
            identifier=parsed.identifier,
            config_source=AgentConfigSource.DISK,
            source_path=parsed.source_path,
            source_rev=parsed.source_rev,
            raw_yaml=parsed.body_yaml,
        )
        return owner.pk, True
    if agent.config_source != AgentConfigSource.DISK:
        raise ValueError('agent is owned by another config source')

    was_disabled = agent.status == AgentStatus.DISABLED
    changed_fields: list[str] = []
    for field, value in (
        ('name', parsed.name),
        ('source_path', parsed.source_path),
        ('status', AgentStatus.ACTIVE),
    ):
        if getattr(agent, field) != value:
            setattr(agent, field, value)
            changed_fields.append(field)
    if changed_fields:
        agent.save(update_fields=changed_fields)

    config_changed = agent.current_config is None or agent.current_config.source_rev != parsed.source_rev
    if config_changed:
        persist_agent_config(
            agent,
            spec,
            source_rev=parsed.source_rev,
            dirty=False,
            raw_yaml=parsed.body_yaml,
        )

    # A removed file disables beat without changing its config revision. Restoring
    # the same bytes must therefore rebuild beat even when no revision is persisted.
    if was_disabled:
        sync_agent_schedule_triggers(agent.id)
    changed = bool(changed_fields) or config_changed
    if changed and not config_changed:
        publish_resource_update_after_commit(owner.pk, 'agents')
    return owner.pk, changed


def sync_agent_path(
    path: Path,
    *,
    root: Path,
    seen_identities: set[tuple[int, str]] | None = None,
) -> SyncItemResult:
    """Parse and synchronize one agent file while containing file-level failures."""
    source_path = _relative_path(path, root)
    try:
        parsed = parse_agent_file(path, root=root)
        owner = resolve_owner(parsed.owner)
        if owner is None:
            raise ValueError('owner not found')
        identity = (int(owner.pk), parsed.identifier)
        if seen_identities is not None and identity in seen_identities:
            logger.error(
                'Duplicate agent identity for %s (owner=%s identifier=%s)',
                source_path,
                parsed.owner,
                parsed.identifier,
            )
            return SyncItemResult(source_path=source_path, success=False, detail='duplicate identity')
        user_id, changed = _persist_parsed_agent(parsed)
        if seen_identities is not None:
            seen_identities.add(identity)
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ConfigValidationError,
        IngestError,
        ValueError,
        IntegrityError,
    ) as exc:
        # Parser details can quote YAML source lines, so logs include only safe metadata.
        logger.error('Agent file sync failed for %s (%s)', source_path, type(exc).__name__)
        return SyncItemResult(source_path=source_path, success=False, detail=type(exc).__name__)
    return SyncItemResult(
        source_path=source_path,
        success=True,
        user_id=user_id,
        changed=changed,
    )


@transaction.atomic
def soft_disable_missing_disk_agents(
    *,
    present_paths: set[str],
    progress: SyncProgressCallback | None = None,
) -> tuple[int, set[int]]:
    """Disable missing agents with optional schedule-sync checkpoints."""
    missing_rows = list(
        Agent.objects.filter(
            config_source=AgentConfigSource.DISK,
            status=AgentStatus.ACTIVE,
        )
        .exclude(source_path__in=present_paths)
        .select_for_update()
        .values_list('id', 'user_id'),
    )
    if not missing_rows:
        return 0, set()

    missing_ids = [agent_id for agent_id, _ in missing_rows]
    user_ids = {user_id for _, user_id in missing_rows}
    Agent.objects.filter(id__in=missing_ids).update(status=AgentStatus.DISABLED)
    for agent_id in missing_ids:
        if progress is not None:
            progress()
        sync_agent_schedule_triggers(agent_id, progress=progress)
        if progress is not None:
            progress()
    for user_id in sorted(user_ids):
        publish_resource_update_after_commit(user_id, 'agents')
    return len(missing_rows), user_ids


def sync_agents_dir(
    *,
    root: Path | None = None,
    progress: SyncProgressCallback | None = None,
) -> SyncReport:
    """Synchronize agent files with optional generic progress checkpoints."""
    resolved_root = root if root is not None else _configured_root()
    if resolved_root is None or not resolved_root.is_dir():
        return SyncReport()

    directory = resolved_root / 'agents'
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
        report.items.append(sync_agent_path(path, root=resolved_root, seen_identities=seen_identities))
        if progress is not None:
            progress()
    if progress is not None:
        progress()
    disabled_count, disabled_user_ids = soft_disable_missing_disk_agents(
        present_paths=present_paths,
        progress=progress,
    )
    report.disabled = disabled_count
    report.disabled_user_ids = disabled_user_ids
    return report
