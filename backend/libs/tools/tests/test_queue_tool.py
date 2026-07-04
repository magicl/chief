# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for QueueTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from libs.tools.queue import QueueTool

from olib.py.django.test.cases import OTestCase


class TestQueueTool(OTestCase):
    def setUp(self) -> None:
        self.tool = QueueTool()
        self.session_id = uuid4()
        self.agent_id = uuid4()
        self.invoke = self.tool.bind(
            user_id=1,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )

    @patch('apps.queues.services.commands.put_item')
    @patch('apps.queues.services.queries.get_queue')
    @patch('apps.agents.models.Agent.objects.get')
    def test_put_delegates_to_commands(self, mock_agent_get: Any, mock_get_queue: Any, mock_put_item: Any) -> None:
        mock_agent_get.return_value = object()
        mock_get_queue.return_value = object()
        item_id = uuid4()
        mock_put_item.return_value = type('PutResult', (), {'item_id': item_id, 'created': True})()

        result = self.invoke(
            'put',
            {
                'owner_agent': 'worker-a',
                'queue': 'inbox',
                'payload': {'hello': 'world'},
            },
        )

        self.assertEqual(result, {'item_id': str(item_id), 'created': True})
        mock_agent_get.assert_called_once_with(user_id=1, identifier='worker-a')
        mock_put_item.assert_called_once()

    @patch('apps.queues.services.commands.take_item')
    @patch('apps.queues.services.queries.get_queue')
    @patch('apps.agents.models.Agent.objects.get')
    def test_take_returns_null_when_empty(self, mock_agent_get: Any, mock_get_queue: Any, mock_take_item: Any) -> None:
        mock_agent_get.return_value = object()
        mock_get_queue.return_value = object()
        mock_take_item.return_value = None

        result = self.invoke('take', {'queue': 'inbox'})

        self.assertEqual(result, {'item': None})

    @patch('apps.queues.services.commands.take_item')
    @patch('apps.queues.services.queries.get_queue')
    @patch('apps.agents.models.Agent.objects.get')
    def test_take_returns_item_payload(self, mock_agent_get: Any, mock_get_queue: Any, mock_take_item: Any) -> None:
        mock_agent_get.return_value = object()
        mock_get_queue.return_value = object()
        item_id = uuid4()
        mock_take_item.return_value = type(
            'TakeResult',
            (),
            {'item_id': item_id, 'payload': {'x': 1}, 'attempt_count': 1},
        )()

        result = self.invoke('take', {'queue': 'inbox'})

        self.assertEqual(
            result,
            {'item_id': str(item_id), 'payload': {'x': 1}, 'attempt': 1},
        )

    @patch('apps.queues.services.commands.complete_item')
    def test_complete_delegates_to_commands(self, mock_complete_item: Any) -> None:
        item_id = uuid4()
        result = self.invoke('complete', {'item_id': str(item_id)})
        self.assertEqual(result, {'ok': True})
        mock_complete_item.assert_called_once_with(item_id=item_id, session_id=self.session_id)

    @patch('apps.queues.services.commands.fail_item')
    def test_fail_delegates_to_commands(self, mock_fail_item: Any) -> None:
        item_id = uuid4()
        result = self.invoke('fail', {'item_id': str(item_id), 'reason': 'bad'})
        self.assertEqual(result, {'ok': True})
        mock_fail_item.assert_called_once_with(
            item_id=item_id,
            session_id=self.session_id,
            reason='bad',
        )
