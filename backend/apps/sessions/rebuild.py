# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Rebuild provider-neutral message list from persisted session events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from apps.sessions.events import events_for
from apps.sessions.models import AgentSession, AgentSessionEventKind
from libs.tools.base import qualified_tool_name


def rebuild_messages_from_events(
    events: Any,
    *,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Replay event records into an OpenAI-style message list."""
    messages: list[dict[str, Any]] = [{'role': 'system', 'content': system_prompt}]
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    for event in events:
        kind = event.kind
        payload = event.payload or {}

        if kind == AgentSessionEventKind.INPUT:
            messages.append({'role': 'user', 'content': payload.get('content', '')})

        elif kind == AgentSessionEventKind.OUTPUT:
            messages.append({'role': 'assistant', 'content': payload.get('content', '')})

        elif kind == AgentSessionEventKind.TOOL_CALL:
            call_id = payload['call_id']
            instance_id = payload.get('instance_id') or payload.get('tool')
            if not isinstance(instance_id, str):
                continue
            tool_name = qualified_tool_name(instance_id, payload['function'])
            call_entry = {
                'id': call_id,
                'type': 'function',
                'function': {
                    'name': tool_name,
                    'arguments': payload.get('arguments', {}),
                },
            }
            pending_tool_calls[call_id] = call_entry
            if messages and messages[-1]['role'] == 'assistant' and 'tool_calls' in messages[-1]:
                messages[-1]['tool_calls'].append(call_entry)
            elif messages and messages[-1]['role'] == 'assistant':
                messages[-1]['tool_calls'] = [call_entry]
                messages[-1].setdefault('content', '')
            else:
                messages.append({'role': 'assistant', 'content': '', 'tool_calls': [call_entry]})

        elif kind == AgentSessionEventKind.TOOL_RESULT:
            call_id = payload['call_id']
            pending_tool_calls.pop(call_id, None)
            messages.append(
                {
                    'role': 'tool',
                    'tool_call_id': call_id,
                    'content': payload.get('content', ''),
                }
            )

    return messages


def rebuild_messages(session: AgentSession | UUID, *, system_prompt: str) -> list[dict[str, Any]]:
    """Replay persisted session events into an OpenAI-style message list (deterministic, ordered by seq).

    Payload shapes (canonical):
    - OUTPUT: ``{"content": str}``
    - INPUT: ``{"content": str}``
    - TOOL_CALL: ``{"call_id": str, "instance_id": str, "type": str, "function": str, "arguments": dict}``
      (legacy events may use ``tool`` instead of ``instance_id``)
    - TOOL_RESULT: ``{"call_id": str, "content": str}``
    - FAILURE / RESTART: metadata only; omitted from the message list
    """
    return rebuild_messages_from_events(events_for(session), system_prompt=system_prompt)
