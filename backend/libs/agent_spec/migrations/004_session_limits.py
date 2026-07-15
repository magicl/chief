# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

FROM_VERSION = 3
TO_VERSION = 4


def upgrade(raw: dict) -> dict:
    """Bump to schema v4; add empty limits block if absent."""
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    if 'limits' not in out or out['limits'] is None:
        out['limits'] = {}
    return out
