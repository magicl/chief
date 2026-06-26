# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import uuid
from unittest.mock import MagicMock, patch

from apps.bus import channels

from olib.py.django.test.cases import OTestCase


class TestMailbox(OTestCase):
    @patch('apps.bus.channels.sync_client')
    def test_push_and_drain(self, mock_sync: MagicMock) -> None:
        client = MagicMock()
        store: list[str] = []

        def rpush(_key: str, value: str) -> None:
            store.append(value)

        def lrange(_key: str, _start: int, _end: int) -> list[str]:
            return list(store)

        def delete(_key: str) -> None:
            store.clear()

        client.rpush.side_effect = rpush
        client.lrange.side_effect = lrange
        client.delete.side_effect = delete
        mock_sync.return_value = client

        sid = uuid.uuid4()
        channels.mailbox_push(sid, {'action': 'chat', 'content': 'hi'})
        drained = channels.mailbox_drain(sid)
        self.assertEqual(drained, [{'action': 'chat', 'content': 'hi'}])
        self.assertEqual(channels.mailbox_drain(sid), [])

    @patch('apps.bus.channels.sync_client')
    def test_lock_acquire_release(self, mock_sync: MagicMock) -> None:
        client = MagicMock()
        locks: dict[str, str] = {}

        def set_(key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:  # noqa: ARG001
            if nx and key in locks:
                return False
            locks[key] = value
            return True

        def get(key: str) -> str | None:
            return locks.get(key)

        def delete(key: str) -> None:
            locks.pop(key, None)

        client.set.side_effect = set_
        client.get.side_effect = get
        client.delete.side_effect = delete
        mock_sync.return_value = client

        sid = uuid.uuid4()
        token = 'tok'
        self.assertTrue(channels.try_acquire_lock(sid, token))
        self.assertFalse(channels.try_acquire_lock(sid, 'other'))
        channels.release_lock(sid, token)
        self.assertTrue(channels.try_acquire_lock(sid, 'other'))
