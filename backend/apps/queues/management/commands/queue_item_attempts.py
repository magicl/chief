# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Print attempt history for a queue item."""

from __future__ import annotations

import argparse
from typing import Any
from uuid import UUID

from apps.queues.services import queries
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Print attempt history for a queue item UUID.'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('item_uuid', type=str, help='QueueItem primary key UUID')

    def handle(self, *args: Any, **options: Any) -> None:
        item_uuid_str = options['item_uuid']
        try:
            item_id = UUID(item_uuid_str)
        except ValueError as exc:
            raise CommandError(f'invalid item UUID: {item_uuid_str}') from exc

        item = queries.get_item(item_id=item_id)
        if item is None:
            raise CommandError(f'queue item not found: {item_uuid_str}')

        attempts = queries.list_attempts_for_item(item_id=item_id)
        self.stdout.write(f'Queue item {item.id} ({item.queue} status={item.status})')
        if not attempts:
            self.stdout.write('No attempts.')
            return

        headers = ('#', 'session', 'outcome', 'started_at', 'ended_at', 'detail')
        rows: list[tuple[str, ...]] = []
        for attempt in attempts:
            rows.append(
                (
                    str(attempt.attempt_number),
                    str(attempt.session_id),
                    attempt.outcome,
                    attempt.started_at.isoformat(),
                    attempt.ended_at.isoformat() if attempt.ended_at else '',
                    (attempt.detail or '').replace('\n', ' '),
                ),
            )

        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        def fmt_row(cells: tuple[str, ...]) -> str:
            """Format one table row with column padding."""
            return '  '.join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

        self.stdout.write(fmt_row(headers))
        self.stdout.write(fmt_row(tuple('-' * w for w in widths)))
        for row in rows:
            self.stdout.write(fmt_row(row))
