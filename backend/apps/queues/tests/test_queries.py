# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for queue read queries."""

from __future__ import annotations

from datetime import timedelta

from apps.queues.models import Queue, QueueItemAttemptOutcome, QueueItemStatus
from apps.queues.services import commands, queries
from apps.queues.tests.base import (
    make_second_session,
    make_test_queue,
    make_test_source,
)
from django.utils import timezone
from libs.web_tables import TableQuery

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


class TestListQueueSummaries(OTransactionTestCase):
    def test_counts_items_by_status_across_all_agent_queues(self) -> None:
        queue_a, session = make_test_queue(identifier='summary-agent', queue_id='alpha')
        queue_b = Queue.objects.create(agent=queue_a.agent, queue_id='beta', agent_config=queue_a.agent_config)
        commands.put_item(queue=queue_a, payload={'n': 1})
        taken = commands.put_item(queue=queue_a, payload={'n': 2})
        commands.take_item(queue=queue_a, session_id=session.id)
        commands.put_item(queue=queue_b, payload={'n': 3})

        summaries = queries.list_queue_summaries(agent=queue_a.agent)

        self.assertEqual([s.queue.queue_id for s in summaries], ['alpha', 'beta'])
        alpha = summaries[0]
        self.assertEqual(alpha.counts['available'], 1)
        self.assertEqual(alpha.counts['taken'], 1)
        self.assertEqual(alpha.counts['done'], 0)
        self.assertEqual(alpha.total, 2)
        beta = summaries[1]
        self.assertEqual(beta.counts['available'], 1)
        self.assertEqual(beta.total, 1)
        del taken

    def test_returns_zeroed_counts_for_empty_queue(self) -> None:
        queue, _session = make_test_queue(identifier='summary-empty-agent')
        summaries = queries.list_queue_summaries(agent=queue.agent)
        self.assertEqual(summaries[0].total, 0)
        self.assertEqual(set(summaries[0].counts.values()), {0})


class TestListSourceIds(OTransactionTestCase):
    def test_lists_ordered_source_ids_for_queue(self) -> None:
        queue, _session = make_test_queue(identifier='source-ids-agent')
        make_test_source(queue, source_id='zeta')
        make_test_source(queue, source_id='alpha')
        self.assertEqual(queries.list_source_ids(queue=queue), ['alpha', 'zeta'])


class TestListQueueItemsPage(OTransactionTestCase):
    def _query(self, **overrides: object) -> TableQuery:
        """Build a TableQuery matching QUEUE_ITEMS_TABLE_SCHEMA, with overridable defaults."""
        defaults: dict[str, object] = {'sort': 'created_at', 'dir': 'desc', 'page': 1, 'filters': {}}
        defaults.update(overrides)
        return TableQuery(**defaults)  # type: ignore[arg-type]

    def test_default_sort_is_created_at_desc(self) -> None:
        queue, _session = make_test_queue(identifier='page-order-agent')
        first = commands.put_item(queue=queue, payload={'n': 1})
        second = commands.put_item(queue=queue, payload={'n': 2})
        page = queries.list_queue_items_page(queue=queue, query=self._query())
        self.assertEqual([item.id for item in page.rows], [second.item_id, first.item_id])

    def test_includes_terminal_statuses(self) -> None:
        queue, session = make_test_queue(identifier='page-terminal-agent')
        put_result = commands.put_item(queue=queue, payload={'n': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        page = queries.list_queue_items_page(queue=queue, query=self._query())

        self.assertEqual({item.id for item in page.rows}, {put_result.item_id})
        self.assertEqual(page.rows[0].status, QueueItemStatus.DONE)

    def test_filters_by_status(self) -> None:
        queue, session = make_test_queue(identifier='page-status-agent')
        # take_item claims the oldest available item first, so the item that stays
        # available is the one put *after* the one that gets taken.
        commands.put_item(queue=queue, payload={'n': 1})
        available = commands.put_item(queue=queue, payload={'n': 2})
        commands.take_item(queue=queue, session_id=session.id)

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'status': 'available'}))

        self.assertEqual({item.id for item in page.rows}, {available.item_id})

    def test_filters_by_source(self) -> None:
        queue, _session = make_test_queue(identifier='page-source-agent')
        source_a = make_test_source(queue, source_id='src-a')
        source_b = make_test_source(queue, source_id='src-b')
        item_a = commands.put_item(queue=queue, payload={'n': 1}, source=source_a, external_id='a-1')
        commands.put_item(queue=queue, payload={'n': 2}, source=source_b, external_id='b-1')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'source': 'src-a'}))

        self.assertEqual({item.id for item in page.rows}, {item_a.item_id})

    def test_search_matches_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='page-q-external-agent')
        source = make_test_source(queue)
        match = commands.put_item(queue=queue, payload={'n': 1}, source=source, external_id='task-alpha')
        commands.put_item(queue=queue, payload={'n': 2}, source=source, external_id='task-beta')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'alpha'}))

        self.assertEqual({item.id for item in page.rows}, {match.item_id})

    def test_search_matches_failure_reason(self) -> None:
        queue, session = make_test_queue(identifier='page-q-failure-agent')
        put_result = commands.put_item(queue=queue, payload={'n': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='rate limited upstream')

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'rate limited'}))

        self.assertEqual({item.id for item in page.rows}, {put_result.item_id})

    def test_search_matches_payload_text(self) -> None:
        queue, _session = make_test_queue(identifier='page-q-payload-agent')
        match = commands.put_item(queue=queue, payload={'note': 'needs-triage'})
        commands.put_item(queue=queue, payload={'note': 'routine'})

        page = queries.list_queue_items_page(queue=queue, query=self._query(filters={'q': 'needs-triage'}))

        self.assertEqual({item.id for item in page.rows}, {match.item_id})

    def test_page_clamps_to_last_page_when_out_of_range(self) -> None:
        queue, _session = make_test_queue(identifier='page-clamp-agent')
        commands.put_item(queue=queue, payload={'n': 1})

        page = queries.list_queue_items_page(queue=queue, query=self._query(page=99))

        self.assertEqual(page.page, 1)
        self.assertEqual(page.total_pages, 1)
        self.assertEqual(len(page.rows), 1)

    def test_page_defaults_to_one_when_total_is_zero(self) -> None:
        queue, _session = make_test_queue(identifier='page-empty-agent')

        page = queries.list_queue_items_page(queue=queue, query=self._query())

        self.assertEqual(page.page, 1)
        self.assertEqual(page.total, 0)
        self.assertEqual(list(page.rows), [])

    def test_sorts_ascending_when_requested(self) -> None:
        queue, _session = make_test_queue(identifier='page-asc-agent')
        first = commands.put_item(queue=queue, payload={'n': 1})
        second = commands.put_item(queue=queue, payload={'n': 2})

        page = queries.list_queue_items_page(queue=queue, query=self._query(sort='created_at', dir='asc'))

        self.assertEqual([item.id for item in page.rows], [first.item_id, second.item_id])
