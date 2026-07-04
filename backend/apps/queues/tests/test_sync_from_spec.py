# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for sync_from_spec materialization."""

from __future__ import annotations

from apps.agents.hardcoded import bootstrap_agent
from apps.agents.models import Agent, AgentConfig
from apps.queues.exceptions import QueueValidationError
from apps.queues.models import Queue, Source, SourceStatus
from apps.queues.services import commands
from django.contrib.auth import get_user_model
from libs.agent_spec import QueueSpec, SourceSpec

from olib.py.django.test.cases import OTransactionTestCase


class TestSyncFromSpec(OTransactionTestCase):
    agent: Agent
    config: AgentConfig

    def setUp(self) -> None:
        super().setUp()
        user = get_user_model().objects.create_user(username='sync-user', password='x')
        self.agent = bootstrap_agent(
            user,
            identifier='sync-agent',
            provider='openai',
            model='gpt-5.4-mini',
        )
        config = self.agent.current_config
        if config is None:
            raise RuntimeError('bootstrap_agent did not set current_config')
        self.config = config

    def test_empty_queues_is_noop(self) -> None:
        commands.sync_from_spec(self.agent, self.config, [])
        self.assertEqual(Queue.objects.filter(agent=self.agent).count(), 0)

    def test_empty_queues_removes_orphan_queues_without_items(self) -> None:
        initial = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='src-a', adapter_type='test', config={})],
            ),
            QueueSpec(id='archive', sources=[]),
        ]
        commands.sync_from_spec(self.agent, self.config, initial)
        self.assertEqual(Queue.objects.filter(agent=self.agent).count(), 2)

        commands.sync_from_spec(self.agent, self.config, [])

        self.assertEqual(Queue.objects.filter(agent=self.agent).count(), 0)
        self.assertEqual(Source.objects.filter(queue__agent=self.agent).count(), 0)

    def test_orphan_queue_with_items_disables_sources(self) -> None:
        initial = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='src-a', adapter_type='test', config={})],
            ),
            QueueSpec(
                id='drop-me',
                sources=[SourceSpec(id='src-b', adapter_type='test', config={})],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, initial)
        drop_queue = Queue.objects.get(agent=self.agent, queue_id='drop-me')
        source = Source.objects.get(queue=drop_queue, source_id='src-b')
        commands.put_item(queue=drop_queue, payload={'x': 1}, source=source, external_id='e1')

        updated = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='src-a', adapter_type='test', config={})],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, updated)

        self.assertTrue(Queue.objects.filter(agent=self.agent, queue_id='drop-me').exists())
        source.refresh_from_db()
        self.assertEqual(source.status, SourceStatus.DISABLED)

    def test_orphan_queue_without_items_is_deleted(self) -> None:
        initial = [
            QueueSpec(id='inbox', sources=[]),
            QueueSpec(id='archive', sources=[]),
        ]
        commands.sync_from_spec(self.agent, self.config, initial)

        updated = [QueueSpec(id='inbox', sources=[])]
        commands.sync_from_spec(self.agent, self.config, updated)

        self.assertFalse(Queue.objects.filter(agent=self.agent, queue_id='archive').exists())

    def test_creates_queue_and_nested_sources(self) -> None:
        queues = [
            QueueSpec(
                id='inbox',
                max_attempts=5,
                sources=[
                    SourceSpec(id='src-a', adapter_type='test', config={'prefix': 'a'}),
                ],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, queues)

        queue = Queue.objects.get(agent=self.agent, queue_id='inbox')
        self.assertEqual(queue.max_attempts, 5)
        self.assertEqual(queue.agent_config_id, self.config.id)

        source = Source.objects.get(queue=queue, source_id='src-a')
        self.assertEqual(source.adapter_type, 'test')
        self.assertEqual(source.config, {'prefix': 'a'})
        self.assertEqual(source.status, SourceStatus.ACTIVE)

    def test_resync_updates_queue_and_sources(self) -> None:
        first = [
            QueueSpec(
                id='inbox',
                max_attempts=3,
                sources=[SourceSpec(id='src-a', adapter_type='test', config={'prefix': 'a'})],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, first)

        config_v2 = AgentConfig.objects.create(
            agent=self.agent,
            source_rev='v2',
            spec_version=1,
            spec={'schema_version': 1},
        )
        second = [
            QueueSpec(
                id='inbox',
                max_attempts=7,
                sources=[
                    SourceSpec(id='src-a', adapter_type='test', config={'prefix': 'b'}),
                    SourceSpec(id='src-b', adapter_type='test', config={'prefix': 'c'}),
                ],
            ),
        ]
        commands.sync_from_spec(self.agent, config_v2, second)

        queue = Queue.objects.get(agent=self.agent, queue_id='inbox')
        self.assertEqual(queue.max_attempts, 7)
        self.assertEqual(queue.agent_config_id, config_v2.id)
        self.assertEqual(Source.objects.filter(queue=queue).count(), 2)
        self.assertEqual(Source.objects.get(queue=queue, source_id='src-a').config, {'prefix': 'b'})

    def test_orphan_source_without_items_is_deleted(self) -> None:
        initial = [
            QueueSpec(
                id='inbox',
                sources=[
                    SourceSpec(id='keep', adapter_type='test', config={}),
                    SourceSpec(id='drop', adapter_type='test', config={}),
                ],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, initial)
        queue = Queue.objects.get(agent=self.agent, queue_id='inbox')

        updated = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='keep', adapter_type='test', config={})],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, updated)

        self.assertFalse(Source.objects.filter(queue=queue, source_id='drop').exists())
        self.assertTrue(Source.objects.filter(queue=queue, source_id='keep').exists())

    def test_orphan_source_with_items_is_disabled(self) -> None:
        initial = [
            QueueSpec(
                id='inbox',
                sources=[
                    SourceSpec(id='keep', adapter_type='test', config={}),
                    SourceSpec(id='disable-me', adapter_type='test', config={}),
                ],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, initial)
        queue = Queue.objects.get(agent=self.agent, queue_id='inbox')
        orphan = Source.objects.get(queue=queue, source_id='disable-me')
        commands.put_item(queue=queue, payload={'x': 1}, source=orphan, external_id='e1')

        updated = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='keep', adapter_type='test', config={})],
            ),
        ]
        commands.sync_from_spec(self.agent, self.config, updated)

        orphan.refresh_from_db()
        self.assertEqual(orphan.status, SourceStatus.DISABLED)

    def test_rejects_empty_adapter_type(self) -> None:
        queues = [
            QueueSpec(
                id='inbox',
                sources=[SourceSpec(id='bad', adapter_type='', config={})],
            ),
        ]
        with self.assertRaises(QueueValidationError):
            commands.sync_from_spec(self.agent, self.config, queues)
