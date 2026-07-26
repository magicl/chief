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

    calls: list[str] = []

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
        """Return compact-poll candidates and record the only allowed source call."""
        del list_id, max_results
        self.calls.append('list_tasks_up_to')
        page = self.list_tasks(list_id='901')
        tasks: list[dict[str, Any]] = page['tasks']
        return tasks

    def get_task(self, task_id: str, **_kwargs: Any) -> dict[str, Any]:
        """Reject expensive source-side full task retrieval."""
        raise AssertionError(f'unexpected get_task for {task_id}')

    def list_comments(self, task_id: str) -> dict[str, Any]:
        """Reject expensive source-side comment retrieval."""
        raise AssertionError(f'unexpected list_comments for {task_id}')


class _ProjectionReadGuard(dict[str, Any]):
    """Track access beyond cheap source dedupe fields while returning adversarial values."""

    def __init__(self, task_id: str) -> None:
        """Seed one task with safe identity and malformed optional projection fields."""
        super().__init__(
            {
                'id': task_id,
                'date_updated': '1750000000000',
                'name': 'Projected task',
                'status': {'status': {'private': True}},
                'assignees': {'private': True},
                'text_content': 'private body',
            }
        )
        self.projection_reads = 0

    def get(self, key: str, default: object = None) -> Any:
        """Count accesses that should happen only after known-id dedupe."""
        if key not in {'id', 'date_updated'}:
            self.projection_reads += 1
        return super().get(key, default)


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
        """Enqueue the shared task summary and stable reference without expensive follow-up calls."""
        _FakeClickUpClient.calls = []
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
        self.assertEqual(
            payload['data'],
            {
                'id': 't1',
                'custom_id': None,
                'name': 'Follow up',
                'status': 'open',
                'assignees': [],
                'priority': None,
                'due_date': None,
                'url': 'https://app.clickup.com/t/t1',
                'date_updated': '1750000000000',
            },
        )
        self.assertNotIn('text_content', payload['data'])
        self.assertNotIn('orderindex', payload['data'])
        self.assertEqual(_FakeClickUpClient.calls, ['list_tasks_up_to'])

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

    def test_poll_skips_non_mapping_entries_and_continues_with_valid_tasks(self) -> None:
        """Ignore malformed provider page entries without aborting later valid task ingestion."""

        class _MixedTaskClient(_FakeClickUpClient):
            def list_tasks_up_to(self, **_kwargs: Any) -> list[Any]:
                """Return malformed entries before one valid task."""
                return [
                    None,
                    'private malformed task',
                    {'id': 'valid', 'name': 'Still ingested', 'date_updated': '1'},
                ]

        seen: list[dict[str, Any]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Record the valid task that survives malformed neighbors."""
            del external_id
            seen.append(payload)
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.clickup.ClickUpClient', _MixedTaskClient):
            result = self.adapter.poll(
                config={'list_id': '901'},
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
            )

        self.assertEqual(result.items_seen, 3)
        self.assertEqual(result.items_enqueued, 1)
        self.assertEqual([payload['data']['id'] for payload in seen], ['valid'])
        self.assertNotIn('private malformed task', repr(seen))

    def test_known_id_dedupe_precedes_summary_projection(self) -> None:
        """Skip known tasks before reading expensive or malformed summary fields."""
        known_task = _ProjectionReadGuard('known')

        class _KnownTaskClient(_FakeClickUpClient):
            def list_tasks_up_to(self, **_kwargs: Any) -> list[dict[str, Any]]:
                """Return the guarded known task."""
                return [known_task]

        with patch('libs.sources.adapters.clickup.ClickUpClient', _KnownTaskClient):
            result = self.adapter.poll(
                config={'list_id': '901'},
                put_item=lambda **_kwargs: PutItemResult(item_id=uuid4(), created=True),
                credential_supplier=lambda: 'pk_test',
                known_external_ids=frozenset({'known'}),
            )

        self.assertEqual(result.items_seen, 1)
        self.assertEqual(result.items_enqueued, 0)
        self.assertEqual(known_task.projection_reads, 0)

    def test_dedupe_disabled_still_projects_guarded_task(self) -> None:
        """Project a guarded task when known-id dedupe is explicitly disabled."""
        task = _ProjectionReadGuard('known')
        seen: list[dict[str, Any]] = []

        class _GuardedTaskClient(_FakeClickUpClient):
            def list_tasks_up_to(self, **_kwargs: Any) -> list[dict[str, Any]]:
                """Return the guarded task for non-deduped ingestion."""
                return [task]

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Record the projected task and update-sensitive external identity."""
            self.assertEqual(external_id, 'known:1750000000000')
            seen.append(payload)
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.clickup.ClickUpClient', _GuardedTaskClient):
            result = self.adapter.poll(
                config={'list_id': '901', 'dedupe': False},
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
                known_external_ids=frozenset({'known'}),
            )

        self.assertEqual(result.items_enqueued, 1)
        self.assertGreater(task.projection_reads, 0)
        self.assertEqual(seen[0]['data']['id'], 'known')
        self.assertIsNone(seen[0]['data']['status'])
        self.assertNotIn('private', repr(seen))
