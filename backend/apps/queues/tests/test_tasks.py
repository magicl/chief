# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for queue Celery tasks."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from apps.queues.models import QueueItem, QueueItemStatus
from apps.queues.services import commands
from apps.queues.tasks import poll_active_sources, poll_source, release_stale_items
from apps.queues.tests.base import make_test_queue, make_test_source
from apps.sessions.models import AgentSessionStatus
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


class TestPollSourceTask(OTransactionTestCase):
    def test_poll_source_enqueues_items(self) -> None:
        queue, _session = make_test_queue(identifier='poll-task-agent')
        source = make_test_source(queue, source_id='poll-src')

        poll_source(str(source.pk))

        items = list(QueueItem.objects.filter(queue=queue, source=source))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, QueueItemStatus.AVAILABLE)
        self.assertTrue(items[0].external_id.startswith('x-'))

        source.refresh_from_db()
        self.assertIsNotNone(source.last_polled_at)
        self.assertIsNone(source.last_error)
        self.assertIsNone(source.last_error_at)

    def test_poll_source_records_adapter_failure(self) -> None:
        queue, _session = make_test_queue(identifier='poll-failure-agent')
        source = make_test_source(queue, source_id='bad-src')
        source.adapter_type = 'missing-adapter'
        source.save(update_fields=['adapter_type'])

        with patch('apps.queues.tasks.logger'):
            poll_source(str(source.pk))

        source.refresh_from_db()
        self.assertIsNone(source.last_polled_at)
        self.assertIsNotNone(source.last_error)
        self.assertIn('unknown adapter type', source.last_error or '')
        self.assertIsNotNone(source.last_error_at)
        self.assertEqual(QueueItem.objects.filter(queue=queue).count(), 0)


class TestPollActiveSourcesTask(OTransactionTestCase):
    def test_poll_active_sources_enqueues_poll_for_each_active_source(self) -> None:
        queue_a, _session_a = make_test_queue(identifier='poll-active-a')
        queue_b, _session_b = make_test_queue(identifier='poll-active-b')
        source_a = make_test_source(queue_a, source_id='active-a')
        source_b = make_test_source(queue_b, source_id='active-b')

        with patch('apps.queues.tasks.poll_source.delay') as mock_delay:
            poll_active_sources()

        self.assertEqual(mock_delay.call_count, 2)
        enqueued = {call.args[0] for call in mock_delay.call_args_list}
        self.assertEqual(enqueued, {str(source_a.pk), str(source_b.pk)})


class TestReleaseStaleItemsTask(OTransactionTestCase):
    def test_release_stale_items_task_releases_held_items(self) -> None:
        queue, session = make_test_queue(identifier='release-task-agent')
        queue.min_hold_seconds = 10
        queue.early_release_seconds = 20
        queue.long_hold_seconds = 3600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        commands.put_item(queue=queue, payload={'task': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        self.assertIsNotNone(take_result)
        item = QueueItem.objects.get(pk=take_result.item_id)  # type: ignore[union-attr]
        item.taken_at = timezone.now() - timedelta(seconds=400)
        item.save(update_fields=['taken_at'])
        session.status = AgentSessionStatus.DONE
        session.save(update_fields=['status'])

        release_stale_items()

        item.refresh_from_db()
        self.assertEqual(item.status, QueueItemStatus.AVAILABLE)
