# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Inbox eval suite discovery."""

# pylint: disable=import-error,wrong-import-position

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from apps.runner.usecases.scenarios import load_usecase_scenario

from olib.py.eval import Sample

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios'
_REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODEL = 'openai/gpt-4o-mini'
ALLOWED_MODELS = (
    'openai/gpt-4o-mini',
    'openai/gpt-4o',
    'anthropic/claude-sonnet-4-5',
)


def resolve_local_root(*, local_root: Path | None = None, environ: dict[str, str] | None = None) -> Path:
    """Resolve the personal overlay root from an explicit path, CHIEF_LOCAL_DIR, or repo `.local`."""
    if local_root is not None:
        return Path(local_root)
    env = environ if environ is not None else os.environ
    configured = str(env.get('CHIEF_LOCAL_DIR', '') or '').strip()
    if configured:
        return Path(configured)
    return _REPO_ROOT / '.local'


def local_inbox_scenario_dir(*, local_root: Path | None = None, environ: dict[str, str] | None = None) -> Path:
    """Return the inbox scenario directory under the personal overlay root."""
    return resolve_local_root(local_root=local_root, environ=environ) / 'evals' / 'inbox' / 'scenarios'


@dataclass(frozen=True)
class InboxEvalSuite:
    """Eval suite that discovers inbox scenario YAML files from disk."""

    scenario_dir: Path = SCENARIO_DIR
    local_scenario_dir: Path | None = field(default=None)

    @property
    def name(self) -> str:
        """Return the stable suite name used for eval partitions."""
        return 'inbox'

    @property
    def default_model(self) -> str:
        """Model id used when eval run omits --model."""
        return DEFAULT_MODEL

    def models(self) -> list[str]:
        """Model ids this suite may run against."""
        return list(ALLOWED_MODELS)

    def samples(self) -> list[Sample]:
        """Load public and optional local overlay scenarios; duplicate ids raise."""
        samples_by_id: dict[str, Sample] = {}
        sources_by_id: dict[str, Path] = {}
        for scenario_dir in self._scenario_dirs():
            for path in sorted(scenario_dir.glob('*.yaml')):
                scenario = load_usecase_scenario(path)
                prior = sources_by_id.get(scenario.id)
                if prior is not None:
                    raise ValueError(
                        f'Duplicate inbox eval scenario id {scenario.id!r}: {prior} and {path}',
                    )
                sources_by_id[scenario.id] = path
                samples_by_id[scenario.id] = Sample(id=scenario.id, payload={'path': str(path)})
        return [samples_by_id[key] for key in sorted(samples_by_id)]

    def _scenario_dirs(self) -> list[Path]:
        """Return existing scenario directories: public first, then local overlay."""
        dirs = [self.scenario_dir]
        local_dir = self.local_scenario_dir if self.local_scenario_dir is not None else local_inbox_scenario_dir()
        if local_dir.is_dir():
            dirs.append(local_dir)
        return dirs


def get_suite() -> InboxEvalSuite:
    """Return the configured inbox eval suite."""
    return InboxEvalSuite()
