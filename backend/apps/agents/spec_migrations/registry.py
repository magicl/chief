# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

import functools
import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.agents.spec_migrations.exceptions import SpecMigrationError

_MIGRATION_RE = re.compile(r'^(\d{3})_(.+)\.py$')


@dataclass(frozen=True)
class SpecMigration:
    from_version: int
    to_version: int
    upgrade: Callable[[dict[str, Any]], dict[str, Any]]
    module_name: str


def _discover_migrations() -> tuple[SpecMigration, ...]:
    migrations_pkg = importlib.import_module('apps.agents.spec_migrations.migrations')
    pkg_file = migrations_pkg.__file__
    if pkg_file is None:
        raise SpecMigrationError('migrations package has no __file__')
    pkg_path = Path(pkg_file).parent
    steps: list[SpecMigration] = []
    for info in sorted(pkgutil.iter_modules([str(pkg_path)])):
        match = _MIGRATION_RE.match(f'{info.name}.py')
        if not match:
            continue
        module = importlib.import_module(f'apps.agents.spec_migrations.migrations.{info.name}')
        expected_to = int(match.group(1))
        from_v = int(module.FROM_VERSION)
        to_v = int(module.TO_VERSION)
        if to_v != expected_to:
            raise SpecMigrationError(f'migration {info.name}: filename prefix {expected_to} != TO_VERSION {to_v}')
        steps.append(
            SpecMigration(
                from_version=from_v,
                to_version=to_v,
                upgrade=module.upgrade,
                module_name=info.name,
            )
        )
    steps.sort(key=lambda s: s.from_version)
    expected = 0
    for step in steps:
        if step.from_version != expected:
            raise SpecMigrationError(
                f'migration gap: expected from_version {expected}, got {step.from_version} ({step.module_name})'
            )
        expected = step.to_version
    return tuple(steps)


@functools.cache
def _cached_migrations() -> tuple[SpecMigration, ...]:
    return _discover_migrations()


def get_spec_migrations() -> tuple[SpecMigration, ...]:
    return _cached_migrations()


def latest_spec_version() -> int:
    steps = get_spec_migrations()
    if not steps:
        return 0
    return steps[-1].to_version
