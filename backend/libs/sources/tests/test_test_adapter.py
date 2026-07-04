# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the test source adapter."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter

from olib.py.django.test.cases import OTestCase


class TestTestSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('test')
        if adapter is None:
            raise RuntimeError('test adapter not registered')
        self.adapter = adapter

    def test_validate_config_accepts_defaults(self) -> None:
        self.adapter.validate_config({})

    def test_validate_config_rejects_bad_prefix(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'prefix': ''})

    def test_validate_config_rejects_bad_batch_size(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'batch_size': 0})

    def test_poll_enqueues_batch_size_items(self) -> None:
        seen: list[tuple[dict[str, Any], str]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        result = self.adapter.poll(
            config={'prefix': 'demo', 'batch_size': 2},
            put_item=put_item,
            credential_supplier=None,
        )

        self.assertEqual(result.items_seen, 2)
        self.assertEqual(result.items_enqueued, 2)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(external_id.startswith('demo-') for _, external_id in seen))
