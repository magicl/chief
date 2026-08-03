# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Parse raw GET-style query parameters into a validated TableQuery for one TableSchema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from libs.web_tables.schema import SortDir, TableSchema


@dataclass(frozen=True, slots=True)
class TableQuery:
    """Validated, schema-allowlisted sort/dir/page/filter state parsed from a request."""

    sort: str
    dir: SortDir
    page: int
    filters: dict[str, str] = field(default_factory=dict)


def parse_table_query(params: Mapping[str, str], schema: TableSchema) -> TableQuery:
    """Parse GET-style *params* into a TableQuery, falling back to *schema* defaults.

    Unknown sort keys, invalid dir values, and non-positive/non-numeric page values
    fall back to schema defaults instead of raising, so a malformed query string
    never turns a list page into a 500. Only ``schema.filter_keys`` are echoed, and
    only when the value is non-empty after stripping whitespace.
    """
    sort = params.get('sort', schema.default_sort)
    if sort not in schema.sort_keys:
        sort = schema.default_sort

    raw_dir = params.get('dir', schema.default_dir)
    dir_: SortDir = raw_dir if raw_dir in ('asc', 'desc') else schema.default_dir  # type: ignore[assignment]

    try:
        page = int(params.get('page', '1'))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    filters: dict[str, str] = {}
    for key in schema.filter_keys:
        value = params.get(key, '').strip()
        if value:
            filters[key] = value

    return TableQuery(sort=sort, dir=dir_, page=page, filters=filters)
