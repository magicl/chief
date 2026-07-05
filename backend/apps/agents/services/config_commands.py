# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Create agents from examples or pasted YAML."""

from __future__ import annotations

import re

from apps.agents.ingest import create_agent_from_spec
from apps.agents.models import Agent
from apps.agents.services.config_sync import spec_content_hash
from apps.agents.services.config_validation import validate_agent_config_yaml
from django.contrib.auth.models import AbstractBaseUser
from libs.agent_specs import load_example_text

from olib.py.utils.uuid7 import uuid7

_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9._-]+$')


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
        raw = load_example_text(slug)
    except FileNotFoundError as exc:
        raise ConfigCommandError(str(exc)) from exc
    spec = validate_agent_config_yaml(raw)
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


def rename_agent(agent: Agent, user_id: int, new_identifier: str) -> None:
    """Rename *agent* when *new_identifier* is valid and unique for the user."""
    cleaned = new_identifier.strip()
    if not cleaned:
        raise ConfigCommandError('identifier required')
    if not _IDENTIFIER_RE.fullmatch(cleaned):
        raise ConfigCommandError(
            'identifier must contain only letters, numbers, dots, underscores, and hyphens',
        )
    if cleaned == agent.identifier:
        return
    if Agent.objects.filter(user_id=user_id, identifier=cleaned).exists():
        raise ConfigCommandError(f'agent {cleaned!r} already exists')
    agent.identifier = cleaned
    agent.save(update_fields=['identifier'])
