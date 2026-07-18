# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Registered credential service types and naming rules."""

from __future__ import annotations

import re

from apps.keys.exceptions import KeyValidationError

SERVICE_TYPES: frozenset[str] = frozenset(
    {
        'openai',
        'anthropic',
        'local_openai',
        'google',
        'dropbox',
        'clickup',
        'obsidian',
    }
)

LLM_SERVICE_TYPES: frozenset[str] = frozenset({'openai', 'anthropic', 'local_openai'})

EXTERNAL_SERVICE_TYPES: frozenset[str] = frozenset({'google', 'dropbox', 'clickup', 'obsidian'})

LLM_ENV_FALLBACK: dict[str, str] = {
    'openai': 'OPENAI_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'local_openai': 'LOCAL_OPENAI_API_KEY',
}

USER_NAMED_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
RESERVED_USER_PREFIXES = ('default:', 'sys:')

MAX_SECRET_BYTES = 16 * 1024


def is_registered_type(type_name: str) -> bool:
    """Return whether ``type_name`` is a known credential service type."""
    return type_name in SERVICE_TYPES


def validate_type(type_name: str) -> str:
    """Validate and return ``type_name``, or raise ``KeyValidationError``."""
    if type_name == 'gmail':
        raise KeyValidationError("credential type 'gmail' was renamed to 'google'; update type: google")
    if not is_registered_type(type_name):
        raise KeyValidationError(f'unknown credential type: {type_name}')
    return type_name


def canonical_default_name(type_name: str) -> str:
    """Return the conventional name for a system default row of ``type_name``."""
    validate_type(type_name)
    return f'default:{type_name}'
