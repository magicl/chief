# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast
from unittest.mock import patch

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.services.commands import create_activity
from apps.sessions.tests.base import make_test_session
from apps.web.tests.test_activity_snapshot import make_parent_with_subagent
from apps.web.views import session_events_sse
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.http import StreamingHttpResponse
from django.test import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class FakePubSub:
    """Provide finite session messages before ending the SSE tail."""

    def __init__(
        self,
        messages: Sequence[dict[str, Any] | None],
        *,
        subscribe_failure: BaseException | None = None,
    ) -> None:
        """Store deterministic Redis messages for one stream connection."""
        self.messages = list(messages)
        self.subscribe_failure = subscribe_failure
        self.subscribed = False
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        """Accept the session-specific subscription."""
        del channel
        if self.subscribe_failure is not None:
            raise self.subscribe_failure
        self.subscribed = True

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> dict[str, Any] | None:
        """Return queued messages, then stop the otherwise infinite stream."""
        assert ignore_subscribe_messages is True
        assert timeout == 1.0
        if self.messages:
            return self.messages.pop(0)
        raise RedisConnectionError('stream complete')

    async def unsubscribe(self, channel: str) -> None:
        """Accept cleanup for the subscribed session channel."""
        del channel
        self.unsubscribed = True

    async def close(self) -> None:
        """Accept pub/sub cleanup."""
        self.closed = True


class FakeRedis:
    """Expose a finite fake pub/sub connection for session SSE tests."""

    def __init__(
        self,
        messages: Sequence[dict[str, Any] | None],
        *,
        subscribe_failure: BaseException | None = None,
    ) -> None:
        """Create the pub/sub object returned by the fake client."""
        self.pubsub_connection = FakePubSub(messages, subscribe_failure=subscribe_failure)
        self.closed = False

    def pubsub(self) -> FakePubSub:
        """Return the stream's fake pub/sub connection."""
        return self.pubsub_connection

    async def close(self) -> None:
        """Accept Redis client cleanup."""
        self.closed = True


class TestSessionActivitiesSse(OTransactionTestCase):
    def _collect(self, session: AgentSession) -> tuple[str, str, str]:
        """Request and fully collect a finite session SSE response."""
        username = session.agent.user.username

        async def collect() -> tuple[str, str, str]:
            """Collect the authenticated async streaming response body."""
            client = AsyncClient()
            user = await sync_to_async(get_user_model().objects.get)(username=username)
            await sync_to_async(client.force_login)(user)
            response = await client.get(f'/sessions/{session.id}/events/')
            assert isinstance(response, StreamingHttpResponse)
            parts: list[bytes] = []
            async for part in cast(AsyncIterator[bytes], response.streaming_content):
                parts.append(part)
            return (
                response['Content-Type'],
                response['X-Accel-Buffering'],
                b''.join(parts).decode(),
            )

        return asyncio.run(collect())

    def _status_for(self, session: AgentSession, *, username: str | None) -> tuple[int, str, str | None]:
        """Request an SSE URL with optional login and return non-stream response data."""

        async def request() -> tuple[int, str, str | None]:
            """Perform one bounded authorization request without consuming a stream."""
            client = AsyncClient()
            if username is not None:
                user = await sync_to_async(get_user_model().objects.get)(username=username)
                await sync_to_async(client.force_login)(user)
            response = await client.get(f'/sessions/{session.id}/events/')
            return (
                response.status_code,
                response.content.decode(),
                response.headers.get('Location'),
            )

        return asyncio.run(request())

    @staticmethod
    def _activity(session: AgentSession, *, seq: int, revision: int, content: str) -> AgentSessionActivity:
        """Create authoritative activity state without invoking notification services."""
        return AgentSessionActivity.objects.create(
            session=session,
            seq=seq,
            revision=revision,
            kind=AgentSessionActivityKind.OUTPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='output',
            summary=content,
            details={'content': content},
        )

    @staticmethod
    def _live_payload(
        session: AgentSession,
        *,
        activity_id: str,
        revision: int,
        content: str,
        seq: int = 1,
    ) -> dict[str, Any]:
        """Build one complete live activity payload without persisting it for replay."""
        return {
            'id': activity_id,
            'session_id': str(session.id),
            'parent_id': None,
            'seq': seq,
            'revision': revision,
            'kind': 'output',
            'status': 'succeeded',
            'name': 'output',
            'summary': content,
            'details': {'content': content},
            'model': None,
            'input_tokens': None,
            'output_tokens': None,
            'cost_usd': None,
            'latency_ms': None,
            'started_at': None,
            'ended_at': None,
            'created_at': '2026-07-26T20:00:00+00:00',
            'child_session_id': None,
        }

    @staticmethod
    def _event_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
        """Parse typed SSE frames into structured event names and JSON payloads."""
        frames: list[tuple[str, dict[str, Any]]] = []
        for block in body.split('\n\n'):
            lines = block.splitlines()
            event_line = next((line for line in lines if line.startswith('event: ')), None)
            data_line = next((line for line in lines if line.startswith('data: ')), None)
            if event_line is not None and data_line is not None:
                frames.append(
                    (
                        event_line.removeprefix('event: '),
                        json.loads(data_line.removeprefix('data: ')),
                    )
                )
        return frames

    def test_replays_current_activity_upserts_in_sequence_order(self) -> None:
        """Reconnect replay sends only current revisions ordered by activity sequence."""
        session = make_test_session('sse-replay')
        second = self._activity(session, seq=2, revision=2, content='second-current')
        first = self._activity(session, seq=1, revision=1, content='first-current')

        content_type, accel_buffering, body = self._collect(session)

        self.assertEqual(content_type, 'text/event-stream')
        self.assertEqual(accel_buffering, 'no')
        frames = self._event_frames(body)
        self.assertEqual([event for event, _payload in frames], ['session_activity', 'session_activity'])
        self.assertEqual(
            [payload['activity']['kind'] for _event, payload in frames],
            ['output', 'output'],
        )
        self.assertEqual(
            {payload['activity']['status'] for _event, payload in frames},
            {'succeeded'},
        )
        self.assertLess(body.index(str(first.id)), body.index(str(second.id)))
        self.assertIn('"operation": "upsert"', body)
        self.assertIn('"revision": 2', body)

    def test_live_activity_accepts_only_newer_revision_and_keeps_session_update(self) -> None:
        """Live tail suppresses stale/equal/foreign upserts but keeps newer and name updates."""
        session = make_test_session('sse-live')
        activity = self._activity(session, seq=1, revision=2, content='current')
        current = activity.to_stream_dict()
        stale = {**current, 'revision': 1, 'details': {'content': 'stale'}}
        equal = {**current, 'details': {'content': 'equal'}}
        newer = {**current, 'revision': 3, 'details': {'content': 'newer'}}
        foreign = {**newer, 'session_id': str(make_test_session('sse-foreign').id), 'details': {'content': 'foreign'}}

        fake = FakeRedis(
            [
                {
                    'type': 'message',
                    'data': json.dumps(
                        {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': stale}}
                    ),
                },
                {
                    'type': 'message',
                    'data': json.dumps(
                        {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': equal}}
                    ),
                },
                {
                    'type': 'message',
                    'data': json.dumps(
                        {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': foreign}}
                    ),
                },
                {
                    'type': 'message',
                    'data': json.dumps(
                        {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': newer}}
                    ),
                },
                {
                    'type': 'message',
                    'data': json.dumps({'channel': 'session_update', 'payload': {'name': 'Renamed'}}),
                },
            ]
        )

        with patch('apps.web.views.async_client', return_value=fake):
            _, _, body = self._collect(session)

        frames = self._event_frames(body)
        self.assertEqual(
            [event for event, _payload in frames],
            ['session_activity', 'session_activity', 'session_update'],
        )
        self.assertEqual(body.count(f'"id": "{activity.id}"'), 2)
        self.assertNotIn('stale', body)
        self.assertNotIn('equal', body)
        self.assertNotIn('foreign', body)
        self.assertIn('newer', body)
        self.assertIn('event: session_update', body)
        self.assertIn('"name": "Renamed"', body)

    def test_idle_poll_emits_heartbeat_comment(self) -> None:
        """Idle polls emit a heartbeat only after the conventional cadence elapses."""
        session = make_test_session('sse-heartbeat')
        fake = FakeRedis([None, None])

        with (
            patch('apps.web.views.async_client', return_value=fake),
            patch('apps.web.views.asyncio.sleep') as mock_sleep,
            patch('apps.web.views.monotonic', side_effect=[100.0, 101.0, 116.0]),
        ):
            _, _, body = self._collect(session)

        self.assertEqual(body, ': heartbeat\n\n')
        self.assertEqual(mock_sleep.await_count, 2)
        mock_sleep.assert_awaited_with(0.1)

    def test_response_releases_database_connection_before_stream_iteration(self) -> None:
        """Return ownership-check connections before the long-lived response starts."""
        session = make_test_session('sse-request-db-release')
        username = session.agent.user.username

        async def request() -> int:
            """Return the database close count observed while creating the response."""
            client = AsyncClient()
            user = await sync_to_async(get_user_model().objects.get)(username=username)
            await sync_to_async(client.force_login)(user)
            with patch('django.db.connections.close_all') as close_all:
                response = await client.get(f'/sessions/{session.id}/events/')
                assert isinstance(response, StreamingHttpResponse)
                return close_all.call_count

        self.assertEqual(asyncio.run(request()), 1)

    def test_replay_releases_database_connection_before_live_tail(self) -> None:
        """Return replay-query connections before waiting indefinitely on Redis."""
        session = make_test_session('sse-replay-db-release')
        fake = FakeRedis([])

        with (
            patch('apps.web.views.async_client', return_value=fake),
            patch('django.db.connections.close_all') as close_all,
        ):
            self._collect(session)

        self.assertEqual(close_all.call_count, 2)

    def test_unauthenticated_request_redirects_without_activity_data(self) -> None:
        """Anonymous clients are redirected before authoritative activity replay."""
        session = make_test_session('sse-anonymous')
        self._activity(session, seq=1, revision=1, content='private-anonymous')

        status, body, location = self._status_for(session, username=None)

        self.assertEqual(status, 302)
        self.assertIsNotNone(location)
        self.assertIn('/admin/login/', location or '')
        self.assertNotIn('private-anonymous', body)

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Not Found: /sessions/[0-9a-f-]+/events/',
                count=1,
            )
        ]
    )
    def test_other_user_request_returns_not_found_without_activity_data(self) -> None:
        """An authenticated non-owner gets not found before activity replay."""
        session = make_test_session('sse-owned')
        self._activity(session, seq=1, revision=1, content='private-owned')
        other = make_test_session('sse-other-user')

        status, body, _ = self._status_for(
            session,
            username=other.agent.user.username,
        )

        self.assertEqual(status, 404)
        self.assertNotIn('private-owned', body)

    def test_live_new_activity_is_accepted_once_and_duplicates_are_suppressed(self) -> None:
        """An unseen activity id is emitted once while equal and stale copies are ignored."""
        session = make_test_session('sse-new-live')
        activity_id = str(uuid.uuid4())
        new_activity = self._live_payload(
            session,
            activity_id=activity_id,
            revision=1,
            content='brand-new',
        )
        equal = {**new_activity, 'details': {'content': 'equal-copy'}}
        stale = {**new_activity, 'revision': 0, 'details': {'content': 'stale-copy'}}
        messages = [
            {
                'type': 'message',
                'data': json.dumps(
                    {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': payload}}
                ),
            }
            for payload in (new_activity, equal, stale)
        ]

        with patch('apps.web.views.async_client', return_value=FakeRedis(messages)):
            _, _, body = self._collect(session)

        self.assertEqual(body.count('event: session_activity'), 1)
        self.assertEqual(body.count(f'"id": "{activity_id}"'), 1)
        self.assertIn('brand-new', body)
        self.assertNotIn('equal-copy', body)
        self.assertNotIn('stale-copy', body)

    def test_subscribes_before_replay_and_deduplicates_buffered_window_messages(self) -> None:
        """Replay-window creates survive while buffered stale updates emit no duplicates."""
        session = make_test_session('sse-replay-window')
        current = self._activity(session, seq=1, revision=2, content='current-replay')
        stale = {**current.to_stream_dict(), 'revision': 1, 'details': {'content': 'stale-buffered'}}
        equal = {**current.to_stream_dict(), 'details': {'content': 'equal-buffered'}}
        new_id = str(uuid.uuid4())
        created = self._live_payload(
            session,
            activity_id=new_id,
            revision=1,
            content='created-buffered',
            seq=2,
        )
        duplicate = {**created, 'details': {'content': 'duplicate-buffered'}}
        messages = [
            {
                'type': 'message',
                'data': json.dumps(
                    {'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': payload}}
                ),
            }
            for payload in (stale, equal, created, duplicate)
        ]
        fake = FakeRedis(messages)

        def replay_after_subscribe(_session_id: Any) -> list[AgentSessionActivity]:
            """Assert the race-closing subscription exists before the DB snapshot."""
            self.assertTrue(fake.pubsub_connection.subscribed)
            return [current]

        with (
            patch('apps.web.views.async_client', return_value=fake),
            patch('apps.web.views.activities_for', side_effect=replay_after_subscribe),
        ):
            _, _, body = self._collect(session)

        self.assertEqual(body.count('event: session_activity'), 2)
        self.assertEqual(body.count(f'"id": "{current.id}"'), 1)
        self.assertEqual(body.count(f'"id": "{new_id}"'), 1)
        self.assertIn('current-replay', body)
        self.assertIn('created-buffered', body)
        self.assertNotIn('stale-buffered', body)
        self.assertNotIn('equal-buffered', body)
        self.assertNotIn('duplicate-buffered', body)

    def test_subscription_transport_failures_fall_back_to_replay_and_cleanup(self) -> None:
        """Expected Redis connection failures still replay and close both resources."""
        session = make_test_session('sse-subscribe-failure')
        activity = self._activity(session, seq=1, revision=1, content='authoritative-replay')
        failures = (
            RedisConnectionError('connection unavailable'),
            RedisTimeoutError('connection timed out'),
            OSError('socket unavailable'),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                fake = FakeRedis([], subscribe_failure=failure)
                with patch('apps.web.views.async_client', return_value=fake):
                    _, _, body = self._collect(session)

                self.assertEqual(body.count('event: session_activity'), 1)
                self.assertIn(str(activity.id), body)
                self.assertTrue(fake.pubsub_connection.closed)
                self.assertTrue(fake.closed)
                self.assertFalse(fake.pubsub_connection.unsubscribed)

    def test_subscription_programming_failure_propagates_after_cleanup(self) -> None:
        """Unexpected subscribe failures are not swallowed, but resources still close."""
        session = make_test_session('sse-subscribe-programming')
        fake = FakeRedis([], subscribe_failure=ValueError('bad subscription call'))

        with (
            patch('apps.web.views.async_client', return_value=fake),
            self.assertRaises(ValueError),
        ):
            self._collect(session)

        self.assertTrue(fake.pubsub_connection.closed)
        self.assertTrue(fake.closed)
        self.assertFalse(fake.pubsub_connection.unsubscribed)

    def test_malformed_live_messages_are_ignored_before_valid_activity(self) -> None:
        """Malformed Redis data cannot stop the stream or leak preceding payloads."""
        session = make_test_session('sse-malformed')
        valid_id = str(uuid.uuid4())
        valid = self._live_payload(
            session,
            activity_id=valid_id,
            revision=1,
            content='valid-after-malformed',
        )
        invalid_revision = {**valid, 'revision': '1', 'details': {'content': 'bad-revision'}}
        invalid_activity = {'operation': 'upsert', 'activity': []}
        malformed: list[dict[str, Any]] = [
            {'type': 'message', 'data': '{'},
            {'type': 'message', 'data': json.dumps([])},
            {'type': 'message', 'data': json.dumps({})},
            {'type': 'message', 'data': json.dumps({'channel': [], 'payload': {}})},
            {'type': 'message', 'data': json.dumps({'channel': 'session_activity'})},
            {'type': 'message', 'data': json.dumps({'channel': 'session_activity', 'payload': []})},
            {
                'type': 'message',
                'data': json.dumps({'channel': 'session_activity', 'payload': invalid_activity}),
            },
            {
                'type': 'message',
                'data': json.dumps(
                    {
                        'channel': 'session_activity',
                        'payload': {'operation': 'upsert', 'activity': invalid_revision},
                    }
                ),
            },
            {'type': 'other', 'data': json.dumps({'private': 'wrong-message-type'})},
            {'type': 'message'},
        ]
        valid_message = {
            'type': 'message',
            'data': json.dumps({'channel': 'session_activity', 'payload': {'operation': 'upsert', 'activity': valid}}),
        }

        with patch('apps.web.views.async_client', return_value=FakeRedis([*malformed, valid_message])):
            _, _, body = self._collect(session)

        self.assertEqual(body.count('event: session_activity'), 1)
        self.assertEqual(body.count(f'"id": "{valid_id}"'), 1)
        self.assertIn('valid-after-malformed', body)
        self.assertNotIn('bad-revision', body)
        self.assertNotIn('wrong-message-type', body)

    def test_replay_list_is_released_before_live_loop(self) -> None:
        """The stream explicitly drops replay model references before indefinite polling."""
        source = inspect.getsource(session_events_sse)

        self.assertLess(source.index('del activities'), source.index('while True'))

    def test_replays_tool_activity_upsert_for_owned_session(self) -> None:
        """Owned-session replay includes tool upserts with the upsert client contract."""
        session = make_test_session('sse-act')
        create_activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='demo.op',
            summary='Tool finished',
            details={},
        )

        _, _, body = self._collect(session)

        self.assertIn('"operation": "upsert"', body)
        self.assertIn('"kind": "tool"', body)

    def test_parent_and_child_replays_remain_session_scoped(self) -> None:
        """Parent replay keeps the subagent link and never child-session activity ids."""
        parent, _child, child_activity_id = make_parent_with_subagent('sse-sep')

        _, _, body = self._collect(parent)

        self.assertNotIn(str(child_activity_id), body)
        self.assertIn('"kind": "subagent"', body)
        self.assertIn('"operation": "upsert"', body)
