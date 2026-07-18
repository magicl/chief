# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for authenticated user-resource SSE delivery and browser wiring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import patch

from apps.bus.resources import user_resource_channel
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.http import StreamingHttpResponse
from django.test import AsyncClient
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from olib.py.django.test.cases import OTransactionTestCase


class FakePubSub:
    """Provide deterministic async pub/sub messages and cleanup observations."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        subscribe_failure: BaseException | None = None,
        read_failure: BaseException | None = None,
        unsubscribe_failure: BaseException | None = None,
        aclose_failure: BaseException | None = None,
    ) -> None:
        """Store the finite message sequence returned by ``get_message``."""
        self.messages = list(messages)
        self.subscribe_failure = subscribe_failure
        self.read_failure = read_failure
        self.unsubscribe_failure = unsubscribe_failure
        self.aclose_failure = aclose_failure
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        """Record the exact subscribed Redis channel."""
        self.subscribed.append(channel)
        if self.subscribe_failure is not None:
            raise self.subscribe_failure

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> dict[str, Any] | None:
        """Return the next queued message without relying on timing."""
        assert ignore_subscribe_messages is True
        assert timeout == 1.0
        if self.read_failure is not None:
            raise self.read_failure
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def unsubscribe(self, channel: str) -> None:
        """Record the exact channel removed during stream cleanup."""
        self.unsubscribed.append(channel)
        if self.unsubscribe_failure is not None:
            raise self.unsubscribe_failure

    async def aclose(self) -> None:
        """Record that the pub/sub object was closed."""
        self.closed = True
        if self.aclose_failure is not None:
            raise self.aclose_failure


class FakeRedis:
    """Expose one fake pub/sub object and client-close state."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        *,
        subscribe_failure: BaseException | None = None,
        read_failure: BaseException | None = None,
        unsubscribe_failure: BaseException | None = None,
        pubsub_aclose_failure: BaseException | None = None,
        aclose_failure: BaseException | None = None,
    ) -> None:
        """Create a client with a deterministic pub/sub message sequence."""
        self.pubsub_instance = FakePubSub(
            messages,
            subscribe_failure=subscribe_failure,
            read_failure=read_failure,
            unsubscribe_failure=unsubscribe_failure,
            aclose_failure=pubsub_aclose_failure,
        )
        self.aclose_failure = aclose_failure
        self.closed = False

    def pubsub(self) -> FakePubSub:
        """Return the client's single pub/sub instance."""
        return self.pubsub_instance

    async def aclose(self) -> None:
        """Record that the Redis client was closed."""
        self.closed = True
        if self.aclose_failure is not None:
            raise self.aclose_failure


class TestResourceEventsSse(OTransactionTestCase):
    """Verify user isolation, validation, and lifecycle of resource SSE."""

    def setUp(self) -> None:
        """Create two users so request-controlled identity can be rejected."""
        self.user = get_user_model().objects.create_user(username='resource-sse-user', password='x')
        self.other = get_user_model().objects.create_user(username='resource-sse-other', password='x')

    def test_anonymous_request_redirects_to_admin_login(self) -> None:
        """Redirect an anonymous stream request using the exact events path."""

        async def request() -> tuple[int, str]:
            """Issue the anonymous request through Django's async client."""
            response = await AsyncClient().get('/events/')
            return response.status_code, response['Location']

        status_code, location = asyncio.run(request())
        self.assertEqual(status_code, 302)
        self.assertEqual(location, '/admin/login/?next=/events/')

    def test_authenticated_response_has_stream_headers(self) -> None:
        """Return SSE headers without opening Redis before iteration."""

        async def request() -> tuple[str, str, str]:
            """Authenticate and inspect the streaming response headers."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            response = await client.get('/events/')
            assert isinstance(response, StreamingHttpResponse)
            return response['Content-Type'], response['Cache-Control'], response['X-Accel-Buffering']

        content_type, cache_control, buffering = asyncio.run(request())
        self.assertEqual(content_type, 'text/event-stream')
        self.assertEqual(cache_control, 'no-cache')
        self.assertEqual(buffering, 'no')

    def test_stream_uses_session_user_and_cleans_up(self) -> None:
        """Ignore query identity, emit one event, and close exact resources."""
        redis = FakeRedis([{'type': 'message', 'data': '{"channel":"resource_update","resource":"agents"}'}])

        async def collect() -> str:
            """Consume one chunk and explicitly close the infinite iterator."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get(f'/events/?user_id={self.other.pk}')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
            return chunk.decode()

        chunk = asyncio.run(collect())
        channel = user_resource_channel(self.user.pk)
        self.assertEqual(redis.pubsub_instance.subscribed, [channel])
        self.assertEqual(redis.pubsub_instance.unsubscribed, [channel])
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)
        self.assertEqual(
            chunk,
            'event: resource_update\ndata: {"channel": "resource_update", "resource": "agents"}\n\n',
        )

    def test_stream_strips_extra_fields_from_valid_message(self) -> None:
        """Emit only the canonical envelope even when Redis includes extra data."""
        redis = FakeRedis(
            [
                {
                    'type': 'message',
                    'data': (
                        '{"channel":"resource_update","resource":"agents",'
                        '"secret":"must-not-leave-redis","owner_id":42}'
                    ),
                }
            ]
        )

        async def collect() -> str:
            """Consume and close one canonicalized resource event."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
            return chunk.decode()

        chunk = asyncio.run(collect())
        self.assertNotIn('must-not-leave-redis', chunk)
        self.assertNotIn('owner_id', chunk)
        self.assertEqual(
            chunk,
            'event: resource_update\ndata: {"channel": "resource_update", "resource": "agents"}\n\n',
        )

    def test_stream_skips_malformed_and_unknown_messages(self) -> None:
        """Skip invalid hints without exposing their raw contents."""
        redis = FakeRedis(
            [
                {'type': 'message', 'data': 'not-json-secret'},
                {'type': 'message', 'data': '{"channel":"other","resource":"agents","secret":"hidden"}'},
                {'type': 'message', 'data': '{"channel":"resource_update","resource":"credentials"}'},
                {'type': 'message', 'data': '{"channel":"resource_update","resource":["agents"]}'},
                {'type': 'message', 'data': '{"channel":"resource_update","resource":{"name":"keys"}}'},
                {'type': 'message', 'data': '{"channel":"resource_update","resource":"keys"}'},
            ]
        )

        async def collect() -> str:
            """Consume the first valid chunk after invalid messages."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
            return chunk.decode()

        with self.assertLogs('apps.web.resource_events', level='DEBUG') as logs:
            chunk = asyncio.run(collect())

        self.assertNotIn('not-json-secret', ''.join(logs.output))
        self.assertNotIn('hidden', ''.join(logs.output))
        channel = user_resource_channel(self.user.pk)
        self.assertEqual(redis.pubsub_instance.unsubscribed, [channel])
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)
        self.assertEqual(
            chunk,
            'event: resource_update\ndata: {"channel": "resource_update", "resource": "keys"}\n\n',
        )

    def test_subscribe_failure_ends_stream_and_closes_resources(self) -> None:
        """Contain Redis subscribe outages and close both lazy resources."""
        redis = FakeRedis([], subscribe_failure=RedisConnectionError('redis unavailable'))

        async def consume() -> list[bytes]:
            """Collect the finite empty stream after subscribe fails."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                return [chunk async for chunk in cast(AsyncIterator[bytes], response.streaming_content)]

        self.assertEqual(asyncio.run(consume()), [])
        self.assertEqual(redis.pubsub_instance.subscribed, [user_resource_channel(self.user.pk)])
        self.assertEqual(redis.pubsub_instance.unsubscribed, [])
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)

    def test_read_failure_ends_stream_and_runs_cleanup(self) -> None:
        """Contain Redis read timeouts after subscribing and cleanly unsubscribe."""
        redis = FakeRedis([], read_failure=RedisTimeoutError('redis timed out'))

        async def consume() -> list[bytes]:
            """Collect the finite empty stream after a subscribed read fails."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                return [chunk async for chunk in cast(AsyncIterator[bytes], response.streaming_content)]

        self.assertEqual(asyncio.run(consume()), [])
        channel = user_resource_channel(self.user.pk)
        self.assertEqual(redis.pubsub_instance.unsubscribed, [channel])
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)

    def test_cleanup_failures_are_contained_and_all_closes_run(self) -> None:
        """Attempt every async close when Redis disappears during cleanup."""
        redis = FakeRedis(
            [{'type': 'message', 'data': '{"channel":"resource_update","resource":"agents"}'}],
            unsubscribe_failure=RedisConnectionError('unsubscribe unavailable'),
            pubsub_aclose_failure=RedisTimeoutError('pubsub close timed out'),
            aclose_failure=RedisConnectionError('client close unavailable'),
        )

        async def consume_one() -> bytes:
            """Consume one event and close despite Redis cleanup failures."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                stream = cast(AsyncIterator[bytes], response.streaming_content)
                chunk = await anext(stream)
                await stream.aclose()
                return chunk

        self.assertIn(b'event: resource_update', asyncio.run(consume_one()))
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)

    def test_stream_preserves_async_cancellation(self) -> None:
        """Propagate task cancellation while still releasing Redis resources."""
        redis = FakeRedis([], read_failure=asyncio.CancelledError())

        async def consume() -> list[bytes]:
            """Attempt to consume a stream whose task is cancelled during read."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', return_value=redis):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                return [chunk async for chunk in cast(AsyncIterator[bytes], response.streaming_content)]

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(consume())
        self.assertTrue(redis.pubsub_instance.closed)
        self.assertTrue(redis.closed)

    def test_missing_redis_configuration_ends_stream(self) -> None:
        """End iteration quietly when Redis is not configured."""

        async def consume() -> list[bytes]:
            """Collect the finite empty stream produced without Redis."""
            client = AsyncClient()
            await sync_to_async(client.force_login)(self.user)
            with patch('apps.web.resource_events.async_client', side_effect=RuntimeError):
                response = await client.get('/events/')
                assert isinstance(response, StreamingHttpResponse)
                return [chunk async for chunk in cast(AsyncIterator[bytes], response.streaming_content)]

        self.assertEqual(asyncio.run(consume()), [])


class TestResourceEventsTemplate(OTransactionTestCase):
    """Verify the authenticated base template's browser event bridge."""

    def setUp(self) -> None:
        """Create the user rendered by authenticated dashboard requests."""
        self.user = get_user_model().objects.create_user(username='resource-template-user', password='x')

    def test_authenticated_page_contains_one_safe_event_source(self) -> None:
        """Wire validated resource hints to htmx through one active source."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        body = response.content.decode()

        self.assertEqual(body.count('new EventSource("/events/")'), 1)
        self.assertIn("addEventListener('resource_update'", body)
        self.assertIn("agents: 'chief:agents-changed'", body)
        self.assertIn("keys: 'chief:keys-changed'", body)
        self.assertIn("message.channel === 'resource_update' && eventName", body)
        self.assertIn('htmx.trigger(document.body, eventName)', body)
        self.assertIn("window.addEventListener('pagehide'", body)
        self.assertIn('resourceEvents.close()', body)
        self.assertIn('try {', body)
        self.assertIn('JSON.parse(event.data)', body)

    def test_persisted_pageshow_reopens_without_duplicate_stream(self) -> None:
        """Reopen after BFCache restore while guarding one active EventSource."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        body = response.content.decode()

        self.assertIn('let resourceEvents = null', body)
        self.assertIn('const openResourceEvents = () =>', body)
        self.assertIn('if (resourceEvents !== null)', body)
        self.assertIn('const closeResourceEvents = () =>', body)
        self.assertIn('resourceEvents = null', body)
        self.assertIn("window.addEventListener('pageshow', (event)", body)
        self.assertIn('if (event.persisted)', body)
        self.assertIn('openResourceEvents()', body)
        self.assertNotIn('{ once: true }', body)

    def test_anonymous_page_has_no_event_source(self) -> None:
        """Avoid opening a resource stream from anonymous pages."""
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'new EventSource(')
