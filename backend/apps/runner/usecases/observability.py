# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Usecase observability hooks for logging and partitioned JSONL eval logs."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from typing import Any

from apps.runner.backends.base import RecordedActivity
from apps.runner.hooks import HookSet

# isort: split

from libs.providers.llm.base import StreamResult, provider_request_failed_message

from olib.py.eval import EventLogWriter, RunPartition

logger = logging.getLogger(__name__)


def build_observability_hooks(
    *,
    partition: RunPartition,
    log_writer: EventLogWriter,
    print_fn: Callable[[str], None] | None = None,
) -> HookSet:
    """Build hooks that mirror runner activity to logging and partitioned JSONL.

    ``print_fn`` is optional for tests; when omitted, messages go to ``logger.info``.
    """
    emit = print_fn or (lambda msg: logger.info('%s', msg))

    def append(record: dict[str, Any]) -> None:
        """Sanitize and validate one strict-JSON event before appending it."""
        safe_record = _json_safe(record)
        if not isinstance(safe_record, dict):
            raise TypeError('observability event must remain an object')
        json.dumps(safe_record, allow_nan=False)
        log_writer.append(partition, safe_record)

    def on_generate_start(messages: list[dict[str, Any]], tool_definitions: list[Any]) -> None:
        """Record that the provider is about to generate a response."""
        emit(f'[generate] messages={len(messages)} tools={len(tool_definitions)}')
        append({'event': 'generate_start', 'message_count': len(messages), 'tool_count': len(tool_definitions)})

    def on_generate_end(result: StreamResult) -> None:
        """Record the generated content summary and tool-call count."""
        preview = _shorten(_safe_text(result.content))
        emit(f'[generate] done content={preview!r} tool_calls={len(result.tool_calls)}')
        provider_failure = (
            {
                'message': provider_request_failed_message(status_code=result.error.status_code),
                'code': result.error.code,
            }
            if result.error is not None
            else None
        )
        append(
            {
                'event': 'generate_end',
                'content': result.content,
                'tool_call_count': len(result.tool_calls),
                'latency_ms': result.latency_ms,
                'error': provider_failure,
            },
        )

    def on_tool_call_start(call: dict[str, Any]) -> None:
        """Record the start of one tool call."""
        emit(f'[tool] {call.get("name", "<unknown>")} start')
        append({'event': 'tool_start', 'call': call})

    def on_tool_call_end(call: dict[str, Any], result_content: str) -> None:
        """Record the completion of one tool call."""
        emit(f'[tool] {call.get("name", "<unknown>")} result={_shorten(_safe_text(result_content))!r}')
        append({'event': 'tool_end', 'call': call, 'result': result_content})

    def on_activity_created(activity: RecordedActivity) -> None:
        """Mirror a newly persisted activity into the eval event log."""
        emit(f'[activity] create {activity.seq} {activity.kind} {activity.status}')
        append({'event': 'session_activity', 'op': 'create', 'record': _activity_record(activity)})

    def on_activity_updated(activity: RecordedActivity) -> None:
        """Mirror an activity revision into the eval event log."""
        emit(f'[activity] update {activity.seq} {activity.kind} rev={activity.revision}')
        append({'event': 'session_activity', 'op': 'update', 'record': _activity_record(activity)})

    return HookSet(
        on_generate_start=on_generate_start,
        on_generate_end=on_generate_end,
        on_tool_call_start=on_tool_call_start,
        on_tool_call_end=on_tool_call_end,
        on_activity_created=on_activity_created,
        on_activity_updated=on_activity_updated,
    )


def _activity_record(activity: RecordedActivity) -> dict[str, Any]:
    """Convert an activity snapshot into JSON-safe observability data."""
    record = activity.to_stream_dict()
    record['details'] = _json_safe(activity.details)
    usage_values = {
        'model': activity.model,
        'input_tokens': activity.input_tokens,
        'output_tokens': activity.output_tokens,
        'cost_usd': str(activity.cost_usd) if activity.cost_usd is not None else None,
        'latency_ms': activity.latency_ms,
    }
    if any(value is not None for value in usage_values.values()):
        record['usage'] = usage_values
    return record


_DROP_KEY_NAMES = frozenset({'exception', 'excinfo', 'stack', 'stacktrace', 'traceback'})
_SENSITIVE_KEY_NAMES = frozenset(
    {
        'apikey',
        'apicredentials',
        'apitoken',
        'auth',
        'authentication',
        'authorization',
        'authorizationheader',
        'authheader',
        'authtoken',
        'bearertoken',
        'clientcredentials',
        'clientsecret',
        'cookie',
        'cookieheader',
        'cookiejar',
        'cookies',
        'creds',
        'credential',
        'credentials',
        'dbpassword',
        'idtoken',
        'passphrase',
        'passwd',
        'password',
        'passwordhash',
        'passwords',
        'refreshtoken',
        'secret',
        'secretkey',
        'secrets',
        'sessioncookie',
        'sessiontoken',
        'setcookie',
        'signingsecret',
        'token',
        'tokens',
        'tokenvalue',
        'userpassword',
        'webhooksecret',
    }
)
_SENSITIVE_KEY_SUFFIXES = (
    'apikey',
    'authorization',
    'cookie',
    'cookies',
    'credential',
    'credentials',
    'password',
    'passwd',
    'secret',
    'token',
)
_MAX_JSON_DEPTH = 32
_REDACTED = '<redacted>'


def _json_safe(
    value: Any,
    *,
    _active: set[int] | None = None,
    _depth: int = 0,
) -> Any:
    """Return bounded strict JSON while redacting structured sensitive values.

    Free-text user/model content remains by design because generic redaction
    cannot distinguish intended content from secrets. Originating provider and
    tool contracts remain responsible for making model-visible text safe.
    """
    if _depth >= _MAX_JSON_DEPTH:
        return '<max-depth>'
    if _active is None:
        _active = set()
    if isinstance(value, dict):
        return _json_safe_mapping(value, active=_active, depth=_depth)
    if isinstance(value, (list, tuple)):
        return _json_safe_sequence(value, active=_active, depth=_depth)
    if isinstance(value, str):
        return _sanitize_json_string(value, active=_active, depth=_depth)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return '<non-finite:NaN>'
        return '<non-finite:Infinity>' if value > 0 else '<non-finite:-Infinity>'
    return _REDACTED


def _json_safe_mapping(value: dict[Any, Any], *, active: set[int], depth: int) -> dict[str, Any] | str:
    """Sanitize one mapping with path-local cycle detection."""
    identity = id(value)
    if identity in active:
        return '<cycle>'
    active.add(identity)
    try:
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = key if isinstance(key, str) else _REDACTED
            normalized = _normalize_key(key_text)
            if normalized in _DROP_KEY_NAMES:
                continue
            if _is_sensitive_key(normalized):
                safe[key_text] = _REDACTED
            else:
                safe[key_text] = _json_safe(item, _active=active, _depth=depth + 1)
        return safe
    finally:
        active.remove(identity)


def _json_safe_sequence(
    value: list[Any] | tuple[Any, ...],
    *,
    active: set[int],
    depth: int,
) -> list[Any] | str:
    """Sanitize one sequence with path-local cycle detection."""
    identity = id(value)
    if identity in active:
        return '<cycle>'
    active.add(identity)
    try:
        return [_json_safe(item, _active=active, _depth=depth + 1) for item in value]
    finally:
        active.remove(identity)


def _sanitize_json_string(value: str, *, active: set[int], depth: int) -> str:
    """Redact structured JSON strings while preserving ordinary free text exactly."""
    stripped = value.strip()
    if not stripped or stripped[0] not in '[{':
        return value
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError):
        return value
    if not isinstance(parsed, (dict, list)):
        return value
    safe = _json_safe(parsed, _active=active, _depth=depth + 1)
    if safe == parsed:
        return value
    return json.dumps(safe, allow_nan=False, separators=(',', ':'), sort_keys=True)


def _normalize_key(key: str) -> str:
    """Normalize case and separators for structured sensitive-key matching."""
    return ''.join(character for character in key.casefold() if character.isalnum())


def _is_sensitive_key(normalized: str) -> bool:
    """Return whether a normalized structured key conventionally holds secrets."""
    return normalized in _SENSITIVE_KEY_NAMES or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)


def _safe_text(value: str) -> str:
    """Return sanitized structured JSON text while preserving ordinary free text."""
    safe = _json_safe(value)
    return safe if isinstance(safe, str) else json.dumps(safe, allow_nan=False)


def _shorten(value: str, *, limit: int = 80) -> str:
    """Return a single-line preview suitable for live terminal logs."""
    compact = value.replace('\n', ' ')
    if len(compact) <= limit:
        return compact
    return f'{compact[: limit - 3]}...'
