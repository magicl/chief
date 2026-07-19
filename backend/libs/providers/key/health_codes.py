# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Stable machine-readable credential health codes (Django-free).

Shared between disk parsing, Django commands, resolve gating, and the Keys UI so
every layer agrees on the same code strings without importing Django models from
this Django-free package.
"""

from __future__ import annotations

VALUE_EMPTY = 'value_empty'
OAUTH_NOT_CONNECTED = 'oauth_not_connected'
INVALID_DECLARATION = 'invalid_declaration'
UNKNOWN_TYPE = 'unknown_type'

ALL_HEALTH_CODES: frozenset[str] = frozenset({VALUE_EMPTY, OAUTH_NOT_CONNECTED, INVALID_DECLARATION, UNKNOWN_TYPE})

HEALTH_CODE_LABELS: dict[str, str] = {
    VALUE_EMPTY: 'Value empty',
    OAUTH_NOT_CONNECTED: 'OAuth not connected',
    INVALID_DECLARATION: 'Invalid declaration',
    UNKNOWN_TYPE: 'Unknown type',
}
