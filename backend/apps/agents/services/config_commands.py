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

_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9._-]+$')


class ConfigCommandError(ValueError):
    """User-facing command failure."""


def normalize_identifier(name: str) -> str:
    """Derive a slug identifier from a human-readable agent name."""
    collapsed = name.strip().lower()
    collapsed = re.sub(r'[\s_]+', '-', collapsed)
    collapsed = re.sub(r'[^a-z0-9.-]+', '', collapsed)
    collapsed = re.sub(r'-+', '-', collapsed).strip('-')
    if not collapsed or not re.search(r'[a-z0-9]', collapsed):
        raise ConfigCommandError('name must contain at least one letter or number')
    return collapsed[:255]


def suggest_identifier(user_id: int, base: str) -> str:
    """Return *base* or the lowest ``base-N`` slug unused by *user_id*."""
    cleaned = base.strip()
    if not _IDENTIFIER_RE.fullmatch(cleaned):
        raise ConfigCommandError('invalid identifier base')
    if not Agent.objects.filter(user_id=user_id, identifier=cleaned).exists():
        return cleaned
    for n in range(2, 10000):
        candidate = f'{cleaned}-{n}'
        if len(candidate) > 255:
            break
        if not Agent.objects.filter(user_id=user_id, identifier=candidate).exists():
            return candidate
    raise ConfigCommandError('too many agents with similar identifiers')


def _validate_identifier(identifier: str) -> str:
    """Return a cleaned identifier or raise when the value is invalid."""
    cleaned = identifier.strip()
    if not cleaned:
        raise ConfigCommandError('identifier required')
    if not _IDENTIFIER_RE.fullmatch(cleaned):
        raise ConfigCommandError(
            'identifier must contain only letters, numbers, dots, underscores, and hyphens',
        )
    return cleaned


def resolve_create_identity(
    user_id: int,
    name: str,
    identifier: str | None = None,
) -> tuple[str, str]:
    """Validate create fields and derive a unique identifier when omitted."""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ConfigCommandError('name required')
    if len(cleaned_name) > 255:
        raise ConfigCommandError('name must be at most 255 characters')
    if identifier:
        cleaned_id = _validate_identifier(identifier)
    else:
        cleaned_id = suggest_identifier(user_id, normalize_identifier(cleaned_name))
    return cleaned_name, cleaned_id


def create_from_example(
    user: AbstractBaseUser,
    slug: str,
    *,
    name: str | None = None,
    identifier: str | None = None,
) -> Agent:
    """Instantiate an agent from a shipped example spec."""
    try:
        raw = load_example_text(slug)
    except FileNotFoundError as exc:
        raise ConfigCommandError(str(exc)) from exc
    spec = validate_agent_config_yaml(raw)
    resolved_name = name or identifier or slug.replace('-', ' ').title()
    resolved_identifier = identifier
    cleaned_name, cleaned_id = resolve_create_identity(
        user.pk,
        resolved_name,
        resolved_identifier,
    )
    return create_agent_from_spec(
        user,
        spec,
        name=cleaned_name,
        identifier=cleaned_id,
        config_source='ui',
        source_rev=f'example:{slug}',
    )


def create_from_yaml(
    user: AbstractBaseUser,
    raw_yaml: str,
    *,
    name: str,
    identifier: str | None = None,
) -> Agent:
    """Create an agent from pasted or uploaded YAML."""
    spec = validate_agent_config_yaml(raw_yaml)
    cleaned_name, cleaned_id = resolve_create_identity(user.pk, name, identifier)
    return create_agent_from_spec(
        user,
        spec,
        name=cleaned_name,
        identifier=cleaned_id,
        config_source='ui',
        source_rev=spec_content_hash(raw_yaml),
    )


def update_agent_profile(
    agent: Agent,
    user_id: int,
    *,
    name: str | None = None,
    identifier: str | None = None,
) -> None:
    """Update display name and/or slug identifier for an owned agent."""
    updates: list[str] = []
    if name is not None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ConfigCommandError('name required')
        if len(cleaned_name) > 255:
            raise ConfigCommandError('name must be at most 255 characters')
        if cleaned_name != agent.name:
            agent.name = cleaned_name
            updates.append('name')
    if identifier is not None:
        cleaned_id = _validate_identifier(identifier)
        if cleaned_id != agent.identifier:
            if Agent.objects.filter(user_id=user_id, identifier=cleaned_id).exclude(pk=agent.pk).exists():
                raise ConfigCommandError(f'agent {cleaned_id!r} already exists')
            agent.identifier = cleaned_id
            updates.append('identifier')
    if updates:
        agent.save(update_fields=updates)


def rename_agent(agent: Agent, user_id: int, new_identifier: str) -> None:
    """Rename *agent* when *new_identifier* is valid and unique for the user."""
    update_agent_profile(agent, user_id, identifier=new_identifier)
