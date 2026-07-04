# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Create agents and sync config from files."""

from __future__ import annotations

from apps.agents.ingest import create_agent_from_spec, persist_agent_config
from apps.agents.models import Agent
from apps.agents.services.config_sync import (
    file_path_from_source,
    read_file_spec_text,
    spec_content_hash,
)
from apps.agents.services.config_validation import validate_agent_config_yaml
from django.contrib.auth.models import AbstractBaseUser
from libs.agent_spec import AgentConfigSpec
from libs.agent_specs import load_example

from olib.py.utils.uuid7 import uuid7


class ConfigCommandError(ValueError):
    """User-facing command failure."""


def create_from_example(
    user: AbstractBaseUser,
    slug: str,
    *,
    identifier: str | None = None,
) -> Agent:
    """Instantiate an agent from a shipped example spec."""
    try:
        spec = load_example(slug)
    except FileNotFoundError as exc:
        raise ConfigCommandError(str(exc)) from exc
    return create_agent_from_spec(
        user,
        spec,
        identifier=identifier or str(uuid7()),
        config_source='ui',
        source_rev=f'example:{slug}',
    )


def create_from_yaml(
    user: AbstractBaseUser,
    raw_yaml: str,
    *,
    identifier: str | None = None,
) -> Agent:
    """Create an agent from pasted or uploaded YAML."""
    spec = validate_agent_config_yaml(raw_yaml)
    return create_agent_from_spec(
        user,
        spec,
        identifier=identifier or str(uuid7()),
        config_source='ui',
        source_rev=spec_content_hash(raw_yaml),
    )


def set_file_source(agent: Agent, path: str, *, sync_now: bool = True) -> Agent:
    """Bind agent to a local YAML file path."""
    path = path.strip()
    if not path.startswith('/'):
        raise ConfigCommandError('File path must be absolute')
    try:
        read_file_spec_text(path)
    except OSError as exc:
        raise ConfigCommandError(f'Cannot read config file: {exc}') from exc
    agent.config_source = f'file:{path}'
    agent.save(update_fields=['config_source'])
    if sync_now:
        sync_from_file(agent)
    return agent


def sync_from_file(agent: Agent) -> AgentConfigSpec | None:
    """Reload config from bound file when content changed; return spec if persisted."""
    path = file_path_from_source(agent.config_source)
    if path is None:
        raise ConfigCommandError('Agent is not file-backed')
    try:
        raw = read_file_spec_text(path)
    except OSError as exc:
        raise ConfigCommandError(f'Cannot read config file: {exc}') from exc
    file_hash = spec_content_hash(raw)
    current = agent.current_config
    if current is not None and current.source_rev == file_hash:
        return None
    spec = validate_agent_config_yaml(raw)
    persist_agent_config(agent, spec, source_rev=file_hash, dirty=False)
    return spec
