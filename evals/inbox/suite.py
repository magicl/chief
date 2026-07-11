# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Inbox eval suite discovery."""

# pylint: disable=import-error,wrong-import-position

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from apps.runner.usecases.scenarios import load_usecase_scenario

from olib.py.eval import Sample

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios'


@dataclass(frozen=True)
class InboxEvalSuite:
    """Eval suite that discovers inbox scenario YAML files from disk."""

    scenario_dir: Path = SCENARIO_DIR

    @property
    def name(self) -> str:
        """Return the stable suite name used for eval partitions."""
        return 'inbox'

    def samples(self) -> list[Sample]:
        """Load all scenario YAML files into eval samples sorted by file name."""
        samples = []
        for path in sorted(self.scenario_dir.glob('*.yaml')):
            scenario = load_usecase_scenario(path)
            samples.append(Sample(id=scenario.id, payload={'path': str(path)}))
        return samples


def get_suite() -> InboxEvalSuite:
    """Return the configured inbox eval suite."""
    return InboxEvalSuite()
