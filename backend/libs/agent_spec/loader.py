# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Load and migrate agent config specs to the latest schema version."""

from __future__ import annotations

from typing import Any

from libs.agent_spec.exceptions import (
    SpecMigrationError,
    UnsupportedSpecVersionError,
)
from libs.agent_spec.registry import get_spec_migrations, latest_spec_version
from libs.agent_spec.spec import AgentConfigSpec


def detect_version(raw: dict[str, Any]) -> int:
    """Infer stored spec version from JSON shape (0 = legacy tool permissions)."""
    if 'schema_version' in raw:
        return int(raw['schema_version'])
    tools = raw.get('tools') or []
    if tools and isinstance(tools[0], dict) and 'tool' in tools[0]:
        return 0
    return 0


def apply_upgrade_chain(raw: dict[str, Any], *, from_version: int) -> dict[str, Any]:
    """Apply registered migration steps from *from_version* through latest."""
    current = dict(raw)
    version = from_version
    for step in get_spec_migrations():
        if step.from_version < version:
            continue
        if step.from_version != version:
            raise SpecMigrationError(f'no migration from version {version}')
        try:
            current = step.upgrade(current)
        except SpecMigrationError:
            raise
        except Exception as exc:
            raise SpecMigrationError(f'migration {step.module_name} failed: {exc}') from exc
        version = step.to_version
    return current


def load_spec_dict(raw: dict[str, Any], *, stored_version: int | None = None) -> dict[str, Any]:
    """Upgrade *raw* dict to the latest schema version in memory."""
    version = stored_version if stored_version is not None else detect_version(raw)
    latest = latest_spec_version()
    if version > latest:
        raise UnsupportedSpecVersionError(f'spec version {version} requires a newer Chief (supports up to {latest})')
    return apply_upgrade_chain(raw, from_version=version)


def load_spec(raw: dict[str, Any], *, stored_version: int | None = None) -> AgentConfigSpec:
    """Parse and upgrade *raw* into a validated ``AgentConfigSpec``."""
    return AgentConfigSpec.model_validate(load_spec_dict(raw, stored_version=stored_version))
