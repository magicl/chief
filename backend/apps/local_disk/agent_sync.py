# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Synchronize local agent YAML files into database-backed agent revisions."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from apps.agents.ingest import IngestError, create_agent_from_spec, persist_agent_config
from apps.agents.models import Agent, AgentStatus
from apps.agents.services.config_validation import ConfigValidationError
from apps.agents.services.schedule_beat import sync_agent_schedule_triggers
from django.db import transaction

from .agent_parse import AgentDiskFile, parse_agent_file
from .key_sync import SyncItemResult, SyncReport
from .owner import resolve_owner
from .paths import resolve_local_root

logger = logging.getLogger(__name__)


def _relative_path(path: Path, root: Path) -> str:
    """Return a safe root-relative path for reports and logs."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


@transaction.atomic
def _persist_parsed_agent(parsed: AgentDiskFile) -> None:
    """Create or revise one parsed disk agent without overwriting another provider."""
    owner = resolve_owner(parsed.owner)
    if owner is None:
        raise ValueError('owner not found')

    agent = Agent.objects.select_for_update().filter(user_id=owner.pk, identifier=parsed.identifier).first()
    if agent is None:
        create_agent_from_spec(
            owner,
            parsed.spec,
            name=parsed.name,
            identifier=parsed.identifier,
            config_source='disk',
            source_path=parsed.source_path,
            source_rev=parsed.source_rev,
            raw_yaml=parsed.body_yaml,
        )
        return
    if agent.config_source != 'disk':
        raise ValueError('agent is owned by another config source')

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

    if agent.current_config is None or agent.current_config.source_rev != parsed.source_rev:
        persist_agent_config(
            agent,
            parsed.spec,
            source_rev=parsed.source_rev,
            dirty=False,
            raw_yaml=parsed.body_yaml,
        )


def sync_agent_path(path: Path, *, root: Path) -> SyncItemResult:
    """Parse and synchronize one agent file while containing file-level failures."""
    source_path = _relative_path(path, root)
    try:
        parsed = parse_agent_file(path, root=root)
        _persist_parsed_agent(parsed)
    except (OSError, UnicodeError, yaml.YAMLError, ConfigValidationError, IngestError, ValueError) as exc:
        # Parser details can quote YAML source lines, so logs include only safe metadata.
        logger.error('Agent file sync failed for %s (%s)', source_path, type(exc).__name__)
        return SyncItemResult(source_path=source_path, success=False, detail=type(exc).__name__)
    return SyncItemResult(source_path=source_path, success=True)


def soft_disable_missing_disk_agents(*, present_paths: set[str]) -> int:
    """Disable active disk agents whose bound files are no longer present."""
    missing_ids = list(
        Agent.objects.filter(
            config_source='disk',
            status=AgentStatus.ACTIVE,
        )
        .exclude(source_path__in=present_paths)
        .values_list('id', flat=True),
    )
    if not missing_ids:
        return 0

    Agent.objects.filter(id__in=missing_ids).update(status=AgentStatus.DISABLED)
    for agent_id in missing_ids:
        sync_agent_schedule_triggers(agent_id)
    return len(missing_ids)


def sync_agents_dir() -> SyncReport:
    """Synchronize all agent YAML files under the configured local root."""
    root = resolve_local_root()
    if root is None or not root.is_dir():
        return SyncReport()

    directory = root / 'agents'
    paths: set[Path] = set()
    if directory.is_dir():
        paths.update(directory.glob('*.yaml'))
        paths.update(directory.glob('*.yml'))

    present_paths = {_relative_path(path, root) for path in paths}
    report = SyncReport(items=[sync_agent_path(path, root=root) for path in sorted(paths)])
    report.disabled = soft_disable_missing_disk_agents(present_paths=present_paths)
    return report
