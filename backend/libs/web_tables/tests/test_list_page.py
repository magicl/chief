# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the ListPage DTO and clamp_page."""

from __future__ import annotations

from libs.web_tables.list_page import ListPage, clamp_page

from olib.py.django.test.cases import OTestCase


def _page(**overrides: object) -> ListPage[object]:
    """Build a small ListPage for tests, with sane overridable defaults."""
    defaults: dict[str, object] = {
        'rows': [],
        'total': 0,
        'page': 1,
        'page_size': 50,
        'sort': 'created_at',
        'dir': 'desc',
        'filters': {},
    }
    defaults.update(overrides)
    return ListPage(**defaults)  # type: ignore[arg-type]


class TestClampPage(OTestCase):
    def test_clamps_high_page_to_last_page(self) -> None:
        self.assertEqual(clamp_page(99, 3), 3)

    def test_clamps_low_page_to_one(self) -> None:
        self.assertEqual(clamp_page(0, 3), 1)
        self.assertEqual(clamp_page(-5, 3), 1)

    def test_clamps_to_one_when_no_pages_exist(self) -> None:
        self.assertEqual(clamp_page(5, 0), 1)

    def test_returns_page_unchanged_when_in_range(self) -> None:
        self.assertEqual(clamp_page(2, 3), 2)


class TestListPage(OTestCase):
    def test_total_pages_is_one_for_empty_table(self) -> None:
        self.assertEqual(_page(total=0, page_size=50).total_pages, 1)

    def test_total_pages_rounds_up(self) -> None:
        self.assertEqual(_page(total=101, page_size=50).total_pages, 3)

    def test_has_previous_and_next_on_a_middle_page(self) -> None:
        page = _page(total=150, page_size=50, page=2)
        self.assertTrue(page.has_previous)
        self.assertTrue(page.has_next)

    def test_first_and_last_page_boundaries(self) -> None:
        first = _page(total=150, page_size=50, page=1)
        last = _page(total=150, page_size=50, page=3)
        self.assertFalse(first.has_previous)
        self.assertFalse(last.has_next)

    def test_start_and_end_index_for_full_page(self) -> None:
        page = _page(total=150, page_size=50, page=2)
        self.assertEqual(page.start_index, 51)
        self.assertEqual(page.end_index, 100)

    def test_start_and_end_index_for_partial_last_page(self) -> None:
        page = _page(total=101, page_size=50, page=3)
        self.assertEqual(page.start_index, 101)
        self.assertEqual(page.end_index, 101)

    def test_start_and_end_index_are_zero_when_empty(self) -> None:
        page = _page(total=0)
        self.assertEqual(page.start_index, 0)
        self.assertEqual(page.end_index, 0)

    def test_query_string_encodes_state_and_filters(self) -> None:
        page = _page(sort='created_at', dir='desc', page=2, filters={'status': 'taken'})
        self.assertEqual(page.query_string(), 'sort=created_at&dir=desc&page=2&status=taken')

    def test_query_string_applies_overrides_without_mutating_page(self) -> None:
        page = _page(sort='created_at', dir='desc', page=2, filters={'status': 'taken'})
        self.assertEqual(
            page.query_string(sort='status', dir='asc', page=1),
            'sort=status&dir=asc&page=1&status=taken',
        )
        self.assertEqual(page.sort, 'created_at')
