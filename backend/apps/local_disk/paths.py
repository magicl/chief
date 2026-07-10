# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Resolve CHIEF_LOCAL_DIR and standard subdirectories."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def resolve_local_root() -> Path | None:
    """Return absolute local root, or None when unset or blank."""
    raw = getattr(settings, 'CHIEF_LOCAL_DIR', '') or ''
    raw = str(raw).strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def keys_dir() -> Path | None:
    """Return ``<root>/keys`` when root is configured."""
    root = resolve_local_root()
    return None if root is None else root / 'keys'


def agents_dir() -> Path | None:
    """Return ``<root>/agents`` when root is configured."""
    root = resolve_local_root()
    return None if root is None else root / 'agents'
