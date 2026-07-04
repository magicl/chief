# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for stale queue item release."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from apps.queues.models import (
    Queue,
    QueueItem,
    QueueItemAttempt,
    QueueItemAttemptOutcome,
    QueueItemStatus,
)
from apps.queues.releasable import is_session_releasable
from apps.queues.services import commands
from apps.queues.tests.base import make_second_session, make_test_queue
from apps.sessions.models import AgentSession, AgentSessionStatus
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


def _take_and_backdate(*, queue: Queue, session: AgentSession, held_seconds: int) -> QueueItem:
    commands.put_item(queue=queue, payload={'task': 1})
    take_result = commands.take_item(queue=queue, session_id=session.id)
    if take_result is None:
        raise AssertionError('expected take to succeed')
    item = QueueItem.objects.get(pk=take_result.item_id)
    item.taken_at = timezone.now() - timedelta(seconds=held_seconds)
    item.save(update_fields=['taken_at'])
    return item


class TestIsSessionReleasable(OTransactionTestCase):
    def test_done_and_waiting_are_releasable(self) -> None:
        _queue, session = make_test_queue(identifier='release-predicate-agent')
        session.status = AgentSessionStatus.DONE
        self.assertTrue(is_session_releasable(session))
        session.status = AgentSessionStatus.WAITING
        self.assertTrue(is_session_releasable(session))

    def test_running_without_ended_at_is_not_releasable(self) -> None:
        _queue, session = make_test_queue(identifier='release-running-agent')
        self.assertEqual(session.status, AgentSessionStatus.RUNNING)
        self.assertIsNone(session.ended_at)
        self.assertFalse(is_session_releasable(session))

    def test_ended_at_makes_session_releasable(self) -> None:
        _queue, session = make_test_queue(identifier='release-ended-agent')
        session.ended_at = timezone.now()
        self.assertTrue(is_session_releasable(session))


class TestReleaseStaleItems(OTransactionTestCase):
    def test_min_hold_prevents_early_release(self) -> None:
        queue, session = make_test_queue(identifier='release-min-hold-agent')
        queue.min_hold_seconds = 60
        queue.early_release_seconds = 120
        queue.long_hold_seconds = 3600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        item = _take_and_backdate(queue=queue, session=session, held_seconds=30)
        session.status = AgentSessionStatus.DONE
        session.save(update_fields=['status'])

        stats = commands.release_stale_items()
        item.refresh_from_db()
        self.assertEqual(stats.released, 0)
        self.assertEqual(stats.exhausted, 0)
        self.assertEqual(item.status, QueueItemStatus.TAKEN)

    def test_early_release_when_session_done_and_held_long_enough(self) -> None:
        queue, session = make_test_queue(identifier='release-early-agent')
        queue.min_hold_seconds = 60
        queue.early_release_seconds = 300
        queue.long_hold_seconds = 3600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        item = _take_and_backdate(queue=queue, session=session, held_seconds=400)
        session.status = AgentSessionStatus.DONE
        session.save(update_fields=['status'])

        stats = commands.release_stale_items()
        item.refresh_from_db()
        self.assertEqual(stats.released, 1)
        self.assertEqual(item.status, QueueItemStatus.AVAILABLE)
        self.assertIsNone(item.taken_by_session_id)
        self.assertIsNone(item.taken_at)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.RELEASED)
        self.assertIsNotNone(attempt.ended_at)

    def test_release_at_max_attempts_marks_exhausted(self) -> None:
        queue, session = make_test_queue(identifier='release-exhaust-agent', max_attempts=1)
        queue.min_hold_seconds = 10
        queue.early_release_seconds = 20
        queue.long_hold_seconds = 3600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        item = _take_and_backdate(queue=queue, session=session, held_seconds=400)
        session.status = AgentSessionStatus.WAITING
        session.save(update_fields=['status'])

        stats = commands.release_stale_items()
        item.refresh_from_db()
        self.assertEqual(stats.exhausted, 1)
        self.assertEqual(item.status, QueueItemStatus.EXHAUSTED)
        self.assertIsNotNone(item.completed_at)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.EXHAUSTED)

    def test_long_hold_releases_even_when_session_still_running(self) -> None:
        queue, session = make_test_queue(identifier='release-long-hold-agent')
        queue.min_hold_seconds = 60
        queue.early_release_seconds = 300
        queue.long_hold_seconds = 600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        item = _take_and_backdate(queue=queue, session=session, held_seconds=700)
        self.assertEqual(session.status, AgentSessionStatus.RUNNING)

        stats = commands.release_stale_items()
        item.refresh_from_db()
        self.assertEqual(stats.released, 1)
        self.assertEqual(item.status, QueueItemStatus.AVAILABLE)

        attempt = QueueItemAttempt.objects.get(item=item, session=session)
        self.assertEqual(attempt.outcome, QueueItemAttemptOutcome.RELEASED)
        self.assertEqual(attempt.detail, 'long_hold_release')

    def test_skips_corrupt_item_and_releases_others(self) -> None:
        queue, session = make_test_queue(identifier='release-corrupt-agent')
        queue.min_hold_seconds = 10
        queue.early_release_seconds = 20
        queue.long_hold_seconds = 3600
        queue.save(update_fields=['min_hold_seconds', 'early_release_seconds', 'long_hold_seconds'])

        bad_item = _take_and_backdate(queue=queue, session=session, held_seconds=400)
        QueueItemAttempt.objects.filter(item=bad_item, session=session).delete()
        session.status = AgentSessionStatus.DONE
        session.save(update_fields=['status'])

        other_session = make_second_session(queue.agent, queue.agent_config)  # type: ignore[arg-type]
        good_item = _take_and_backdate(queue=queue, session=other_session, held_seconds=400)
        other_session.status = AgentSessionStatus.DONE
        other_session.save(update_fields=['status'])

        with patch('apps.queues.services.commands.logger'):
            stats = commands.release_stale_items()
        good_item.refresh_from_db()
        bad_item.refresh_from_db()

        self.assertEqual(stats.released, 1)
        self.assertEqual(good_item.status, QueueItemStatus.AVAILABLE)
        self.assertEqual(bad_item.status, QueueItemStatus.TAKEN)
