# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import asyncio
from collections.abc import AsyncIterator
from typing import cast

from apps.sessions.events import append_event
from apps.sessions.models import AgentSessionEventKind
from apps.sessions.tests.base import make_test_session
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.http import StreamingHttpResponse
from django.test import AsyncClient

from olib.py.django.test.cases import OTransactionTestCase


class TestSessionEventsSse(OTransactionTestCase):
    def test_replays_persisted_events_from_db(self) -> None:
        session = make_test_session('sse-agent')
        append_event(session, AgentSessionEventKind.INPUT, {'content': 'ping'})
        append_event(session, AgentSessionEventKind.OUTPUT, {'content': 'pong'})

        async def collect() -> tuple[str, str, str]:
            client = AsyncClient()
            user = await sync_to_async(get_user_model().objects.get)(username='user-sse-agent')
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

        content_type, accel_buffering, body = asyncio.run(collect())
        self.assertEqual(content_type, 'text/event-stream')
        self.assertEqual(accel_buffering, 'no')
        self.assertIn('"kind": "INPUT"', body)
        self.assertIn('"kind": "OUTPUT"', body)
        self.assertIn('"seq": 1', body)
        self.assertIn('"seq": 2', body)

        self.assertIn('"kind": "INPUT"', body)
        self.assertIn('"kind": "OUTPUT"', body)
        self.assertIn('"seq": 1', body)
        self.assertIn('"seq": 2', body)
