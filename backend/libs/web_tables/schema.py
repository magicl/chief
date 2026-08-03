# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Table schema declarations for the generic filterable-table query contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SortDir = Literal['asc', 'desc']


@dataclass(frozen=True, slots=True)
class TableSchema:
    """Declare one table's allowlisted sort keys, defaults, page size, and filter keys.

    Sort keys are opaque strings the caller controls (e.g. UI column ids); mapping
    a sort key to a concrete ORM ``order_by`` expression is the domain query's job,
    not this Django-free schema's job.
    """

    sort_keys: frozenset[str]
    default_sort: str
    default_dir: SortDir
    page_size: int
    filter_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Fail fast on an internally inconsistent schema rather than at query time."""
        if self.default_sort not in self.sort_keys:
            raise ValueError(f'default_sort {self.default_sort!r} must be one of sort_keys')
        if self.page_size < 1:
            raise ValueError('page_size must be at least 1')
