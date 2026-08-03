# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Paginated table result DTO shared by domain queries and web views/templates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar
from urllib.parse import urlencode

from libs.web_tables.schema import SortDir

RowT = TypeVar('RowT')


def clamp_page(page: int, total_pages: int) -> int:
    """Clamp a 1-based *page* into ``[1, total_pages]``; ``total_pages < 1`` clamps to 1."""
    if total_pages < 1:
        return 1
    return min(max(page, 1), total_pages)


@dataclass(frozen=True, slots=True)
class ListPage(Generic[RowT]):
    """One rendered page of a filtered/sorted table, plus the query state that produced it."""

    rows: Sequence[RowT]
    total: int
    page: int
    page_size: int
    sort: str
    dir: SortDir
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        """Total page count; always at least 1, even for an empty table."""
        if self.total <= 0:
            return 1
        return -(-self.total // self.page_size)

    @property
    def has_previous(self) -> bool:
        """Whether a page before the current one exists."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Whether a page after the current one exists."""
        return self.page < self.total_pages

    @property
    def start_index(self) -> int:
        """1-based index of this page's first row, or 0 when the table is empty."""
        if self.total == 0:
            return 0
        return (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        """1-based index of this page's last row, or 0 when the table is empty."""
        if self.total == 0:
            return 0
        return min(self.page * self.page_size, self.total)

    def query_string(
        self,
        *,
        sort: str | None = None,
        dir: SortDir | None = None,  # noqa: A002 - mirrors the `dir` query parameter name
        page: int | None = None,
    ) -> str:
        """Encode this page's sort/dir/page/filters as a query string, with optional overrides.

        Templates use this to build sort-header and pagination links without
        duplicating the query-parameter contract; callers only override the one
        axis they are changing (e.g. just ``page``, or just ``sort``/``dir``).
        """
        params: dict[str, str] = {
            'sort': sort if sort is not None else self.sort,
            'dir': dir if dir is not None else self.dir,
            'page': str(page if page is not None else self.page),
        }
        params.update(self.filters)
        return urlencode(params)
