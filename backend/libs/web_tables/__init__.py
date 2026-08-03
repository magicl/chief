# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic server-side table query parsing and paginated list-page DTOs.

Django-free so both ``apps.web`` views and domain ``apps.*.services.queries``
modules can share one filter/sort/pagination contract without a layering
violation (domain apps must not import ``apps.web``).
"""

from __future__ import annotations

from libs.web_tables.list_page import ListPage, clamp_page
from libs.web_tables.query import TableQuery, parse_table_query
from libs.web_tables.schema import SortDir, TableSchema

__all__ = [
    'ListPage',
    'SortDir',
    'TableQuery',
    'TableSchema',
    'clamp_page',
    'parse_table_query',
]
