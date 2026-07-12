# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

FROM_VERSION = 2
TO_VERSION = 3


def upgrade(raw: dict) -> dict:
    """Bump to schema v3 and ensure an ``integrations`` list exists."""
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    if 'integrations' not in out or out['integrations'] is None:
        out['integrations'] = []
    return out
