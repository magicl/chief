# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Model constraint tests for apps.queues."""

from apps.queues.models import Queue, QueueItem, QueueItemStatus, Source
from apps.queues.tests.base import make_test_queue, make_test_source
from django.db import IntegrityError

from olib.py.django.test.cases import OTransactionTestCase


class TestQueueModels(OTransactionTestCase):
    def test_queue_unique_per_agent_queue_id(self) -> None:
        queue, _session = make_test_queue(identifier='queue-uniq-agent', queue_id='inbox')
        with self.assertRaises(IntegrityError):
            Queue.objects.create(
                agent=queue.agent,
                queue_id='inbox',
                agent_config=queue.agent_config,
            )

    def test_source_unique_per_queue_source_id(self) -> None:
        queue, _session = make_test_queue(identifier='source-uniq-agent')
        make_test_source(queue, source_id='poll')
        with self.assertRaises(IntegrityError):
            Source.objects.create(
                queue=queue,
                source_id='poll',
                adapter_type='test',
            )

    def test_queue_item_unique_per_source_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='item-uniq-agent')
        source = make_test_source(queue, source_id='src-a')
        QueueItem.objects.create(
            queue=queue,
            source=source,
            external_id='msg-1',
            payload={'n': 1},
        )
        with self.assertRaises(IntegrityError):
            QueueItem.objects.create(
                queue=queue,
                source=source,
                external_id='msg-1',
                payload={'n': 2},
            )

    def test_manual_items_allow_duplicate_empty_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='manual-dup-agent')
        QueueItem.objects.create(queue=queue, payload={'a': 1})
        QueueItem.objects.create(queue=queue, payload={'a': 2})
        self.assertEqual(
            QueueItem.objects.filter(queue=queue, source__isnull=True).count(),
            2,
        )

    def test_terminal_status_values(self) -> None:
        queue, _session = make_test_queue(identifier='status-agent')
        item = QueueItem.objects.create(
            queue=queue,
            status=QueueItemStatus.DONE,
            payload={},
        )
        self.assertEqual(item.status, QueueItemStatus.DONE)
