# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Inbox evaluation suite exports."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from evals.inbox.suite import get_suite  # noqa: E402


def get_sample_runner() -> object:
    """Return the configured inbox sample runner, loading Django-backed runner code lazily."""
    from evals.inbox.runner import get_sample_runner as _get_sample_runner

    return _get_sample_runner()


__all__ = ['get_sample_runner', 'get_suite']
