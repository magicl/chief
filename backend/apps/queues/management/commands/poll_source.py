# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Management command to poll a queue source."""

from __future__ import annotations

import argparse
from typing import Any
from uuid import UUID

from apps.agents.models import Agent
from apps.queues.models import Queue, Source
from apps.queues.tasks import poll_source
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Poll a queue source and enqueue new items via its adapter.'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--source-pk', type=str, help='Source UUID primary key')
        parser.add_argument('--agent-id', type=str, help='Agent identifier (with --queue-id and --source-id)')
        parser.add_argument('--queue-id', type=str, help='Queue id within the agent')
        parser.add_argument('--source-id', type=str, help='Source id within the queue')

    def handle(self, *args: Any, **options: Any) -> None:
        source_pk = options.get('source_pk')
        agent_id = options.get('agent_id')
        queue_id = options.get('queue_id')
        source_id = options.get('source_id')

        if source_pk:
            if any((agent_id, queue_id, source_id)):
                raise CommandError('Use either --source-pk or --agent-id/--queue-id/--source-id, not both')
            source = self._get_source_by_pk(source_pk)
        elif agent_id and queue_id and source_id:
            source = self._get_source_by_names(agent_id=agent_id, queue_id=queue_id, source_id=source_id)
        else:
            raise CommandError(
                'Provide --source-pk or all of --agent-id, --queue-id, and --source-id',
            )

        poll_source(str(source.pk))
        self.stdout.write(self.style.SUCCESS(f'Polled source {source.pk}'))

    def _get_source_by_pk(self, source_pk: str) -> Source:
        """Resolve a source by UUID primary key."""
        try:
            return Source.objects.get(pk=UUID(source_pk))
        except (Source.DoesNotExist, ValueError) as exc:
            raise CommandError(f'source not found: {source_pk}') from exc

    def _get_source_by_names(self, *, agent_id: str, queue_id: str, source_id: str) -> Source:
        """Resolve a source by agent identifier and queue/source slugs."""
        try:
            agent = Agent.objects.get(identifier=agent_id)
        except Agent.DoesNotExist as exc:
            raise CommandError(f'agent not found: {agent_id}') from exc
        try:
            queue = Queue.objects.get(agent=agent, queue_id=queue_id)
        except Queue.DoesNotExist as exc:
            raise CommandError(f'queue not found: {agent_id}/{queue_id}') from exc
        try:
            return Source.objects.get(queue=queue, source_id=source_id)
        except Source.DoesNotExist as exc:
            raise CommandError(f'source not found: {agent_id}/{queue_id}/{source_id}') from exc
