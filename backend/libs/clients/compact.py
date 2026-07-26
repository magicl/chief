# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shared limits and helpers for compact integration projections."""

from collections.abc import Sequence
from typing import Any, TypeVar

BODY_CHAR_LIMIT = 32_000
ATTACHMENT_LIMIT = 25
CLICKUP_COMMENT_LIMIT = 10
CLICKUP_COMMENT_CHAR_LIMIT = 4_000
CLICKUP_SUBTASK_LIMIT = 25

ItemT = TypeVar('ItemT')


def truncate_text(
    text: str,
    *,
    limit: int,
    ref: dict[str, Any] | None = None,
) -> tuple[str, dict[str, object] | None]:
    """Bound text length and describe any omitted suffix; assumes a non-negative limit."""
    if len(text) <= limit:
        return text, None

    metadata: dict[str, object] = {
        'truncated': True,
        'omitted_chars': len(text) - limit,
    }
    if ref is not None:
        metadata['ref'] = ref
    return text[:limit], metadata


def bound_items(
    items: Sequence[ItemT],
    *,
    limit: int,
    total: int | None = None,
) -> tuple[list[ItemT], dict[str, int | bool]]:
    """Bound items assuming a non-negative limit and a supplied total at least len(items)."""
    bounded = list(items[:limit])
    resolved_total = len(items) if total is None else total
    omitted_count = max(0, resolved_total - len(bounded))
    metadata: dict[str, int | bool] = {
        'truncated': omitted_count > 0,
        'included': len(bounded),
        'total': resolved_total,
        'omitted_count': omitted_count,
    }
    return bounded, metadata


def advisory(*, code: str, message: str) -> dict[str, str]:
    """Build a stable advisory record for compact projections."""
    return {'code': code, 'message': message}
