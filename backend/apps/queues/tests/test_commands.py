# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for queue write commands."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from apps.queues.exceptions import (
    QueueItemNotFoundError,
    QueueItemStateError,
    QueueNotTakerError,
    QueuePayloadTooLargeError,
    QueueValidationError,
)
from apps.queues.models import (
    QueueItem,
    QueueItemAttempt,
    QueueItemAttemptOutcome,
    QueueItemStatus,
)
from apps.queues.services import commands
from apps.queues.tests.base import (
    make_second_session,
    make_test_queue,
    make_test_source,
)
from django.db import IntegrityError
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


class TestPutItem(OTransactionTestCase):
    def test_put_creates_available_item(self) -> None:
        queue, _session = make_test_queue(identifier='put-create-agent')
        result = commands.put_item(queue=queue, payload={'hello': 'world'})
        item = QueueItem.objects.get(pk=result.item_id)
        self.assertTrue(result.created)
        self.assertEqual(item.status, QueueItemStatus.AVAILABLE)
        self.assertEqual(item.payload, {'hello': 'world'})

    def test_put_with_source_requires_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='put-src-agent')
        source = make_test_source(queue)
        with self.assertRaises(QueueValidationError):
            commands.put_item(queue=queue, payload={'x': 1}, source=source)

    def test_put_dedupes_on_source_and_external_id(self) -> None:
        queue, _session = make_test_queue(identifier='put-dedup-agent')
        source = make_test_source(queue)
        first = commands.put_item(
            queue=queue,
            payload={'n': 1},
            source=source,
            external_id='ext-1',
        )
        second = commands.put_item(
            queue=queue,
            payload={'n': 2},
            source=source,
            external_id='ext-1',
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.item_id, second.item_id)
        item = QueueItem.objects.get(pk=first.item_id)
        self.assertEqual(item.payload, {'n': 1})

    def test_put_handles_integrity_error_race(self) -> None:
        queue, _session = make_test_queue(identifier='put-race-agent')
        source = make_test_source(queue)
        existing = commands.put_item(
            queue=queue,
            payload={'n': 1},
            source=source,
            external_id='race-1',
        )
        item = QueueItem.objects.get(pk=existing.item_id)

        with (
            patch.object(QueueItem.objects, 'filter') as mock_filter,
            patch.object(QueueItem.objects, 'create', side_effect=IntegrityError()),
            patch.object(QueueItem.objects, 'get', return_value=item) as mock_get,
        ):
            mock_filter.return_value.first.return_value = None
            result = commands.put_item(
                queue=queue,
                payload={'n': 2},
                source=source,
                external_id='race-1',
            )

        self.assertFalse(result.created)
        self.assertEqual(result.item_id, existing.item_id)
        mock_get.assert_called_once_with(source=source, external_id='race-1')

    def test_put_terminal_item_is_idempotent(self) -> None:
        queue, session = make_test_queue(identifier='put-terminal-agent')
        source = make_test_source(queue)
        put_result = commands.put_item(
            queue=queue,
            payload={'task': 1},
            source=source,
            external_id='done-1',
        )
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        again = commands.put_item(
            queue=queue,
            payload={'task': 2},
            source=source,
            external_id='done-1',
        )
        self.assertFalse(again.created)
        self.assertEqual(again.item_id, put_result.item_id)
        item = QueueItem.objects.get(pk=put_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.DONE)
        self.assertEqual(item.payload, {'task': 1})

    def test_put_rejects_oversized_payload(self) -> None:
        queue, _session = make_test_queue(identifier='put-size-agent')
        huge = {'data': 'x' * 70000}
        with self.assertRaises(QueuePayloadTooLargeError):
            commands.put_item(queue=queue, payload=huge)

    def test_put_accepts_payload_at_limit(self) -> None:
        queue, _session = make_test_queue(identifier='put-limit-agent')
        payload = {'data': 'x' * (65536 - len(json.dumps({'data': ''}).encode('utf-8')))}
        result = commands.put_item(queue=queue, payload=payload)
        self.assertTrue(result.created)


class TestTakeItem(OTransactionTestCase):
    def test_take_creates_attempt_row(self) -> None:
        queue, session = make_test_queue(identifier='take-attempt-agent')
        put_result = commands.put_item(queue=queue, payload={'work': True})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        self.assertEqual(take_result.item_id, put_result.item_id)
        self.assertEqual(take_result.payload, {'work': True})
        self.assertEqual(take_result.attempt_count, 1)

        item = QueueItem.objects.get(pk=put_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.TAKEN)
        self.assertEqual(item.taken_by_session_id, session.id)
        self.assertEqual(item.attempt_count, 1)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.attempt_number, 1)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.IN_PROGRESS)
        self.assertIsNone(attempt.ended_at)

    def test_take_on_empty_queue_returns_none(self) -> None:
        queue, session = make_test_queue(identifier='take-empty-agent')
        self.assertIsNone(commands.take_item(queue=queue, session_id=session.id))
        self.assertIsNone(commands.take_item(queue=queue, session_id=session.id))

    def test_take_skips_item_when_max_attempts_would_be_exceeded(self) -> None:
        queue, session = make_test_queue(identifier='take-exhaust-agent', max_attempts=1)
        exhausted = commands.put_item(queue=queue, payload={'old': 1})
        item = QueueItem.objects.get(pk=exhausted.item_id)
        item.attempt_count = 1
        item.status = QueueItemStatus.AVAILABLE
        item.save(update_fields=['attempt_count', 'status'])

        fresh = commands.put_item(queue=queue, payload={'new': 2})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        self.assertEqual(take_result.item_id, fresh.item_id)

        item.refresh_from_db()
        self.assertEqual(item.status, QueueItemStatus.EXHAUSTED)

    def test_only_one_session_claims_item(self) -> None:
        queue, session_a = make_test_queue(identifier='take-lock-agent')
        assert queue.agent_config is not None
        session_b = make_second_session(queue.agent, queue.agent_config)
        commands.put_item(queue=queue, payload={'shared': True})

        first = commands.take_item(queue=queue, session_id=session_a.id)
        second = commands.take_item(queue=queue, session_id=session_b.id)
        assert first is not None
        self.assertIsNone(second)
        self.assertEqual(QueueItemAttempt.objects.filter(item_id=first.item_id).count(), 1)

    def test_take_rejects_session_from_other_agent(self) -> None:
        queue, _session_a = make_test_queue(identifier='take-owner-a')
        _other_queue, session_b = make_test_queue(identifier='take-owner-b')
        commands.put_item(queue=queue, payload={'shared': True})

        with self.assertRaises(QueueValidationError):
            commands.take_item(queue=queue, session_id=session_b.id)


class TestCompleteItem(OTransactionTestCase):
    def test_complete_closes_attempt(self) -> None:
        queue, session = make_test_queue(identifier='complete-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        item = QueueItem.objects.get(pk=take_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.DONE)
        self.assertIsNotNone(item.completed_at)
        self.assertIsNone(item.taken_by_session_id)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.COMPLETED)
        self.assertIsNotNone(attempt.ended_at)

    def test_complete_rejects_non_taker(self) -> None:
        queue, session_a = make_test_queue(identifier='complete-taker-agent')
        assert queue.agent_config is not None
        session_b = make_second_session(queue.agent, queue.agent_config)
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session_a.id)
        assert take_result is not None

        with self.assertRaises(QueueNotTakerError):
            commands.complete_item(item_id=take_result.item_id, session_id=session_b.id)

    def test_complete_unknown_item_raises(self) -> None:
        queue, session = make_test_queue(identifier='complete-missing-agent')
        with self.assertRaises(QueueItemNotFoundError):
            commands.complete_item(item_id=queue.id, session_id=session.id)


class TestFailItem(OTransactionTestCase):
    def test_fail_closes_attempt_with_reason(self) -> None:
        queue, session = make_test_queue(identifier='fail-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='bad input')

        item = QueueItem.objects.get(pk=take_result.item_id)
        self.assertEqual(item.status, QueueItemStatus.FAILED)
        self.assertEqual(item.failure_reason, 'bad input')
        self.assertIsNotNone(item.completed_at)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.FAILED)
        self.assertEqual(attempt.detail, 'bad input')
        self.assertIsNotNone(attempt.ended_at)

    def test_fail_rejects_non_taker(self) -> None:
        queue, session_a = make_test_queue(identifier='fail-taker-agent')
        assert queue.agent_config is not None
        session_b = make_second_session(queue.agent, queue.agent_config)
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session_a.id)
        assert take_result is not None

        with self.assertRaises(QueueNotTakerError):
            commands.fail_item(item_id=take_result.item_id, session_id=session_b.id)

    def test_fail_on_available_item_raises(self) -> None:
        queue, session = make_test_queue(identifier='fail-state-agent')
        put_result = commands.put_item(queue=queue, payload={'x': 1})
        with self.assertRaises(QueueItemStateError):
            commands.fail_item(item_id=put_result.item_id, session_id=session.id)


class TestQueueResourceHints(OTransactionTestCase):
    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_put_item_publishes_scoped_hint_for_new_item(self, publish: MagicMock) -> None:
        queue, _session = make_test_queue(identifier='hint-put-agent')
        publish.reset_mock()

        commands.put_item(queue=queue, payload={'x': 1})

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_take_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-take-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        publish.reset_mock()

        commands.take_item(queue=queue, session_id=session.id)

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_complete_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-complete-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        publish.reset_mock()

        commands.complete_item(item_id=take_result.item_id, session_id=session.id)

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_fail_item_publishes_scoped_hint(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-fail-agent')
        commands.put_item(queue=queue, payload={'x': 1})
        take_result = commands.take_item(queue=queue, session_id=session.id)
        assert take_result is not None
        publish.reset_mock()

        commands.fail_item(item_id=take_result.item_id, session_id=session.id, reason='bad')

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_release_stale_items_publishes_hint_for_released_item(self, publish: MagicMock) -> None:
        queue, session = make_test_queue(identifier='hint-release-agent')
        put_result = commands.put_item(queue=queue, payload={'x': 1})
        commands.take_item(queue=queue, session_id=session.id)
        item = QueueItem.objects.get(pk=put_result.item_id)
        item.taken_at = timezone.now() - timedelta(seconds=queue.long_hold_seconds + 1)
        item.save(update_fields=['taken_at'])
        publish.reset_mock()

        commands.release_stale_items()

        publish.assert_called_once_with(queue.agent.user_id, 'queues', agent_id=queue.agent_id, queue_id=queue.id)

    @patch('apps.queues.services.commands.publish_resource_update_after_commit')
    def test_sync_from_spec_publishes_agent_scoped_hint(self, publish: MagicMock) -> None:
        # A queue-unscoped hint is expected even when reconciliation only removes
        # orphan queues, since the Queues section still needs a refetch.
        queue, _session = make_test_queue(identifier='hint-sync-agent')
        agent = queue.agent
        config = queue.agent_config
        assert config is not None
        publish.reset_mock()

        commands.sync_from_spec(agent, config, [])

        publish.assert_called_once_with(agent.user_id, 'queues', agent_id=agent.id)
