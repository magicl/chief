# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Canonical readiness result for pre-dispatch condition probes.

Lives in ``libs`` (Django-free) so tool readiness probes and the ``apps.agents``
trigger block gate share one type without ``libs`` importing ``apps``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockResult:
    """One condition's readiness plus an operator-facing reason.

    ``reason`` is rendered to operators and written to logs, so it must never carry
    secrets, credentials, or raw failure text from an underlying provider.
    """

    ready: bool
    reason: str = ''
