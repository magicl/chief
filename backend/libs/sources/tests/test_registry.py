# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for source adapter registry."""

from __future__ import annotations

from libs.sources.registry import all_adapters, get_adapter

from olib.py.django.test.cases import OTestCase


class TestSourceRegistry(OTestCase):
    def test_get_test_adapter(self) -> None:
        adapter = get_adapter('test')
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.adapter_type, 'test')

    def test_unknown_adapter_returns_none(self) -> None:
        self.assertIsNone(get_adapter('missing-adapter'))

    def test_all_adapters_includes_test(self) -> None:
        adapters = all_adapters()
        self.assertIn('test', adapters)
