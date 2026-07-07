# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the ClickUp source adapter (client stubbed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter

from olib.py.django.test.cases import OTestCase


class _FakeClickUpClient:
    """Stand-in for ClickUpClient returning canned tasks."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def list_tasks(self, *, list_id: str, **_kwargs: Any) -> dict[str, Any]:
        del list_id
        return {
            'tasks': [
                {
                    'id': 't1',
                    'name': 'Follow up',
                    'status': {'status': 'open'},
                    'url': 'https://app.clickup.com/t/t1',
                    'date_updated': '1750000000000',
                    'text_content': 'body',
                },
            ],
            'last_page': True,
        }

    def list_tasks_up_to(self, *, list_id: str, max_results: int, **_kwargs: Any) -> list[dict[str, Any]]:
        del list_id, max_results
        page = self.list_tasks(list_id='901')
        tasks: list[dict[str, Any]] = page['tasks']
        return tasks


class TestClickUpSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('clickup')
        if adapter is None:
            raise RuntimeError('clickup adapter not registered')
        self.adapter = adapter

    def test_validate_config_requires_list_id(self) -> None:
        self.adapter.validate_config({'list_id': '901'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({})

    def test_poll_enqueues_envelope_with_ref(self) -> None:
        seen: list[tuple[dict[str, Any], str]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.clickup.ClickUpClient', _FakeClickUpClient):
            result = self.adapter.poll(
                config={'list_id': '901', 'team_id': '9', 'max_results': 50},
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
            )

        self.assertEqual(result.items_seen, 1)
        self.assertEqual(result.items_enqueued, 1)
        payload, external_id = seen[0]
        self.assertEqual(external_id, 't1')
        self.assertEqual(payload['ref'], {'service': 'clickup', 'resource_type': 'task', 'resource_id': 't1'})
        self.assertEqual(payload['data']['name'], 'Follow up')
        self.assertEqual(payload['data']['status'], 'open')

    def test_poll_uses_updated_external_id_when_dedupe_disabled(self) -> None:
        seen: list[str] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            del payload
            seen.append(external_id)
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.clickup.ClickUpClient', _FakeClickUpClient):
            self.adapter.poll(
                config={'list_id': '901', 'team_id': '9', 'max_results': 50, 'dedupe': False},
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
            )

        self.assertEqual(seen, ['t1:1750000000000'])
