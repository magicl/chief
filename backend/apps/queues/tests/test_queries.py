# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for queue read queries."""

from __future__ import annotations

from datetime import timedelta

from apps.queues.models import Queue, QueueItemAttemptOutcome, QueueItemStatus
from apps.queues.services import commands, queries
from apps.queues.tests.base import make_second_session, make_test_queue
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


class TestQueueQueries(OTransactionTestCase):
    def test_get_queue_by_agent_and_slug(self) -> None:
        queue, _session = make_test_queue(identifier='query-get-queue-agent', queue_id='inbox')
        found = queries.get_queue(agent=queue.agent, queue_id='inbox')
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, queue.id)
        self.assertIsNone(queries.get_queue(agent=queue.agent, queue_id='missing'))

    def test_list_queues_for_agent(self) -> None:
        queue, _session = make_test_queue(identifier='query-list-agent', queue_id='alpha')
        Queue.objects.create(agent=queue.agent, queue_id='beta', agent_config=queue.agent_config)
        names = [q.queue_id for q in queries.list_queues(agent=queue.agent)]
        self.assertEqual(names, ['alpha', 'beta'])

    def test_get_item_and_list_queue_items(self) -> None:
        queue, _session = make_test_queue(identifier='query-items-agent')
        put_a = commands.put_item(queue=queue, payload={'n': 1})
        put_b = commands.put_item(queue=queue, payload={'n': 2})
        item = queries.get_item(item_id=put_a.item_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.payload, {'n': 1})

        available = queries.list_queue_items(queue=queue, status=QueueItemStatus.AVAILABLE)
        self.assertEqual({i.id for i in available}, {put_a.item_id, put_b.item_id})

    def test_list_attempts_for_item_returns_all_sessions(self) -> None:
        queue, session_a = make_test_queue(identifier='query-attempts-agent', max_attempts=3)
        assert queue.agent_config is not None
        session_b = make_second_session(queue.agent, queue.agent_config)
        put_result = commands.put_item(queue=queue, payload={'work': True})

        first = commands.take_item(queue=queue, session_id=session_a.id)
        assert first is not None
        item = queries.get_item(item_id=put_result.item_id)
        assert item is not None
        item.taken_at = timezone.now() - timedelta(seconds=queue.long_hold_seconds + 1)
        item.save(update_fields=['taken_at'])
        commands.release_stale_items()

        second = commands.take_item(queue=queue, session_id=session_b.id)
        assert second is not None

        attempts = queries.list_attempts_for_item(item_id=put_result.item_id)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].session_id, session_a.id)
        self.assertEqual(attempts[0].outcome, QueueItemAttemptOutcome.RELEASED)
        self.assertEqual(attempts[1].session_id, session_b.id)
        self.assertEqual(attempts[1].outcome, QueueItemAttemptOutcome.IN_PROGRESS)
