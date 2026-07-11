# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Usecase observability hooks for live terminal output and JSONL eval logs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.runner.backends.base import RecordedEvent
from apps.runner.hooks import HookSet

# isort: split

from libs.providers.llm.base import StreamResult

from olib.py.eval import EventLogWriter, RunPartition


def build_observability_hooks(
    *,
    partition: RunPartition,
    log_writer: EventLogWriter,
    print_fn: Callable[[str], None] = print,
) -> HookSet:
    """Build hooks that mirror runner activity to terminal text and partitioned JSONL."""

    def append(record: dict[str, Any]) -> None:
        """Append one event-log record for this usecase partition."""
        log_writer.append(partition, record)

    def on_generate_start(messages: list[dict[str, Any]], tool_definitions: list[Any]) -> None:
        """Record that the provider is about to generate a response."""
        print_fn(f'[generate] messages={len(messages)} tools={len(tool_definitions)}')
        append({'event': 'generate_start', 'message_count': len(messages), 'tool_count': len(tool_definitions)})

    def on_generate_end(result: StreamResult) -> None:
        """Record the generated content summary and tool-call count."""
        preview = _shorten(result.content)
        print_fn(f'[generate] done content={preview!r} tool_calls={len(result.tool_calls)}')
        append(
            {
                'event': 'generate_end',
                'content': result.content,
                'tool_call_count': len(result.tool_calls),
                'latency_ms': result.latency_ms,
                'error': result.error.__dict__ if result.error is not None else None,
            },
        )

    def on_tool_call_start(call: dict[str, Any]) -> None:
        """Record the start of one tool call."""
        print_fn(f'[tool] {call.get("name", "<unknown>")} start')
        append({'event': 'tool_start', 'call': call})

    def on_tool_call_end(call: dict[str, Any], result_content: str) -> None:
        """Record the completion of one tool call."""
        print_fn(f'[tool] {call.get("name", "<unknown>")} result={_shorten(result_content)!r}')
        append({'event': 'tool_end', 'call': call, 'result': result_content})

    def on_event(event: RecordedEvent) -> None:
        """Record one persisted session event emitted by the runner backend."""
        print_fn(f'[event] {event.seq} {event.kind}')
        append({'event': 'session_event', 'record': _event_record(event)})

    return HookSet(
        on_generate_start=on_generate_start,
        on_generate_end=on_generate_end,
        on_tool_call_start=on_tool_call_start,
        on_tool_call_end=on_tool_call_end,
        on_event=on_event,
    )


def _event_record(event: RecordedEvent) -> dict[str, Any]:
    """Convert a RecordedEvent into a JSON-serializable log payload without session context."""
    return {
        'id': str(event.event_id),
        'seq': event.seq,
        'kind': event.kind,
        'payload': event.payload,
        'model': event.model,
        'input_tokens': event.input_tokens,
        'output_tokens': event.output_tokens,
        'cost_usd': str(event.cost_usd) if event.cost_usd is not None else None,
        'latency_ms': event.latency_ms,
        'created_at': event.created_at.isoformat() if event.created_at else None,
    }


def _shorten(value: str, *, limit: int = 80) -> str:
    """Return a single-line preview suitable for live terminal logs."""
    compact = value.replace('\n', ' ')
    if len(compact) <= limit:
        return compact
    return f'{compact[: limit - 3]}...'
