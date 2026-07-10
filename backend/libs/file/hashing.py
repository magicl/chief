# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Stable content hashing for file change detection."""

from __future__ import annotations

import hashlib


def normalize_bytes(raw: str | bytes) -> bytes:
    """Normalize text to UTF-8 LF bytes for stable hashing."""
    if isinstance(raw, bytes):
        text = raw.decode('utf-8')
    else:
        text = raw
    return text.replace('\r\n', '\n').encode('utf-8')


def content_hash(raw: str | bytes) -> str:
    """Return a prefixed SHA-256 digest for normalized file contents."""
    digest = hashlib.sha256(normalize_bytes(raw)).hexdigest()
    return f'sha256:{digest}'
