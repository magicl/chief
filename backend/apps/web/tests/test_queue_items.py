# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the agent Queues section and the queue items page/partial views."""

from __future__ import annotations

import logging

from apps.agents.services.config_commands import create_from_example
from apps.queues.services import commands
from apps.queues.tests.base import make_test_queue, make_test_source
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestAgentQueuesPartial(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_requires_login(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-auth-agent')
        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))
        self.assertEqual(response.status_code, 302)

    def test_lists_owned_queue_with_counts_and_link(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-list-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'n': 1})
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))

        self.assertContains(response, 'inbox')
        self.assertContains(
            response,
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
        )

    def test_shows_empty_state_without_queues(self) -> None:
        user = get_user_model().objects.create_user(username='queues-partial-empty-user', password='test')
        agent = create_from_example(user, 'clock-assistant', identifier='queues-partial-empty-agent')
        self.client.force_login(user)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': agent.id}))

        self.assertContains(response, 'No queues configured')

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/partials/queues/$', count=1)]
    )
    def test_rejects_foreign_agent(self) -> None:
        queue, _session = make_test_queue(identifier='queues-partial-foreign-agent')
        other = get_user_model().objects.create_user(username='queues-partial-foreign-user', password='test')
        self.client.force_login(other)

        response = self.client.get(reverse('agent_queues_partial', kwargs={'agent_id': queue.agent_id}))

        self.assertEqual(response.status_code, 404)


class TestAgentDetailQueuesSection(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_agent_detail_includes_queues_section(self) -> None:
        queue, _session = make_test_queue(identifier='detail-queues-agent', queue_id='inbox')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('agent_detail', kwargs={'agent_id': queue.agent_id}))

        self.assertContains(response, 'Queues')
        self.assertContains(response, 'inbox')
        self.assertContains(response, 'id="agent-queues"')


class TestQueueItemsView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_requires_login(self) -> None:
        queue, _session = make_test_queue(identifier='items-auth-agent', queue_id='inbox')
        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))
        self.assertEqual(response.status_code, 302)

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/queues/inbox/$', count=1)]
    )
    def test_rejects_foreign_agent(self) -> None:
        queue, _session = make_test_queue(identifier='items-foreign-agent', queue_id='inbox')
        other = get_user_model().objects.create_user(username='items-foreign-user', password='test')
        self.client.force_login(other)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertEqual(response.status_code, 404)

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /agents/[0-9a-f-]+/queues/missing/$', count=1)]
    )
    def test_unknown_queue_slug_returns_not_found(self) -> None:
        queue, _session = make_test_queue(identifier='items-missing-agent', queue_id='inbox')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'missing'}))

        self.assertEqual(response.status_code, 404)

    def test_renders_all_statuses_and_columns(self) -> None:
        queue, session = make_test_queue(identifier='items-render-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'available-one'})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='bad input')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'available-one')
        self.assertContains(response, 'bad input')
        self.assertContains(response, 'id="queue-items-table"')

    def test_filters_by_status_via_query_param(self) -> None:
        queue, session = make_test_queue(identifier='items-filter-agent', queue_id='inbox')
        # take_item claims the oldest available item first, so put the item that
        # gets taken before the item that stays available.
        commands.put_item(queue=queue, payload={'note': 'gets-taken'})
        commands.put_item(queue=queue, payload={'note': 'stays-available'})
        commands.take_item(queue=queue, session_id=session.id)
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'status': 'taken'},
        )

        self.assertContains(response, 'gets-taken')
        self.assertNotContains(response, 'stays-available')

    def test_invalid_sort_param_falls_back_without_failing(self) -> None:
        queue, _session = make_test_queue(identifier='items-invalid-sort-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'x'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'sort': 'not-a-real-column'},
        )

        self.assertEqual(response.status_code, 200)

    def test_page_out_of_range_clamps_instead_of_failing(self) -> None:
        queue, _session = make_test_queue(identifier='items-page-clamp-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'only-item'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'page': '99'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'only-item')

    def test_source_dropdown_lists_queue_sources(self) -> None:
        queue, _session = make_test_queue(identifier='items-source-options-agent', queue_id='inbox')
        make_test_source(queue, source_id='gmail-main')
        self.client.force_login(queue.agent.user)

        response = self.client.get(reverse('queue_items', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}))

        self.assertContains(response, 'gmail-main')


class TestQueueItemsPartial(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_returns_only_table_fragment(self) -> None:
        queue, _session = make_test_queue(identifier='items-partial-agent', queue_id='inbox')
        commands.put_item(queue=queue, payload={'note': 'fragment-item'})
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items_partial', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
        )

        self.assertContains(response, 'fragment-item')
        self.assertNotContains(response, 'id="queue-items-table"')
        self.assertNotContains(response, 'frame-header')

    def test_partial_honors_current_query_string(self) -> None:
        queue, session = make_test_queue(identifier='items-partial-filter-agent', queue_id='inbox')
        # take_item claims the oldest available item first, so put the item that
        # gets taken before the item that stays available.
        commands.put_item(queue=queue, payload={'note': 'gets-taken'})
        commands.put_item(queue=queue, payload={'note': 'stays-available'})
        commands.take_item(queue=queue, session_id=session.id)
        self.client.force_login(queue.agent.user)

        response = self.client.get(
            reverse('queue_items_partial', kwargs={'agent_id': queue.agent_id, 'queue_id': 'inbox'}),
            {'status': 'available'},
        )

        self.assertContains(response, 'stays-available')
        self.assertNotContains(response, 'gets-taken')
