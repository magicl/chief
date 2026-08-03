# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for TableSchema validation and TableQuery parsing."""

from __future__ import annotations

from libs.web_tables.query import TableQuery, parse_table_query
from libs.web_tables.schema import TableSchema

from olib.py.django.test.cases import OTestCase


def _schema(**overrides: object) -> TableSchema:
    """Build a small TableSchema for tests, with sane overridable defaults."""
    defaults: dict[str, object] = {
        'sort_keys': frozenset({'created_at', 'status'}),
        'default_sort': 'created_at',
        'default_dir': 'desc',
        'page_size': 50,
        'filter_keys': frozenset({'status', 'q'}),
    }
    defaults.update(overrides)
    return TableSchema(**defaults)  # type: ignore[arg-type]


class TestTableSchema(OTestCase):
    def test_rejects_default_sort_outside_allowlist(self) -> None:
        with self.assertRaises(ValueError):
            TableSchema(sort_keys=frozenset({'a'}), default_sort='b', default_dir='asc', page_size=10)

    def test_rejects_non_positive_page_size(self) -> None:
        with self.assertRaises(ValueError):
            TableSchema(sort_keys=frozenset({'a'}), default_sort='a', default_dir='asc', page_size=0)


class TestParseTableQuery(OTestCase):
    def test_defaults_when_params_are_empty(self) -> None:
        query = parse_table_query({}, _schema())
        self.assertEqual(query, TableQuery(sort='created_at', dir='desc', page=1, filters={}))

    def test_accepts_allowlisted_sort_and_dir(self) -> None:
        query = parse_table_query({'sort': 'status', 'dir': 'asc'}, _schema())
        self.assertEqual(query.sort, 'status')
        self.assertEqual(query.dir, 'asc')

    def test_falls_back_to_default_sort_for_unknown_key(self) -> None:
        query = parse_table_query({'sort': 'unknown-column'}, _schema())
        self.assertEqual(query.sort, 'created_at')

    def test_falls_back_to_default_dir_for_invalid_value(self) -> None:
        query = parse_table_query({'dir': 'sideways'}, _schema())
        self.assertEqual(query.dir, 'desc')

    def test_falls_back_to_page_one_for_non_numeric_page(self) -> None:
        query = parse_table_query({'page': 'nope'}, _schema())
        self.assertEqual(query.page, 1)

    def test_falls_back_to_page_one_for_non_positive_page(self) -> None:
        self.assertEqual(parse_table_query({'page': '0'}, _schema()).page, 1)
        self.assertEqual(parse_table_query({'page': '-3'}, _schema()).page, 1)

    def test_accepts_valid_page_number(self) -> None:
        query = parse_table_query({'page': '4'}, _schema())
        self.assertEqual(query.page, 4)

    def test_echoes_only_declared_non_empty_filters(self) -> None:
        query = parse_table_query({'status': 'taken', 'source': 'ignored-undeclared', 'q': '  '}, _schema())
        self.assertEqual(query.filters, {'status': 'taken'})
