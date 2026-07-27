# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Rebuild provider-neutral messages from canonical session activities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Protocol
from uuid import UUID

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.services.queries import activities_for
from libs.tools.base import qualified_tool_name


class RebuildActivity(Protocol):
    """Structural activity fields required by provider-message reconstruction."""

    @property
    def seq(self) -> int:
        """Immutable provider replay order."""

    @property
    def kind(self) -> str:
        """Canonical activity kind."""

    @property
    def status(self) -> str:
        """Canonical activity lifecycle status."""

    @property
    def details(self) -> Any:
        """Kind-specific provider-visible details."""


def _tool_result_content(result: Any) -> str | None:
    """Return provider-safe deterministic text for a persisted tool result."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _tool_messages(details: Any, status: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Expand one complete unified tool activity into provider wire messages."""
    if status not in {
        AgentSessionActivityStatus.SUCCEEDED,
        AgentSessionActivityStatus.FAILED,
    }:
        return None
    if not isinstance(details, dict) or 'result' not in details or 'arguments' not in details:
        return None

    call_id = details.get('call_id')
    instance_id = details.get('instance_id') or details.get('instance') or details.get('tool')
    function = details.get('function')
    arguments = details.get('arguments')
    if (
        not isinstance(call_id, str)
        or not call_id
        or not isinstance(instance_id, str)
        or not instance_id
        or not isinstance(function, str)
        or not function
        or not isinstance(arguments, dict)
    ):
        return None

    content = _tool_result_content(details['result'])
    if content is None:
        return None
    call_entry = {
        'id': call_id,
        'type': 'function',
        'function': {
            'name': qualified_tool_name(instance_id, function),
            'arguments': arguments,
        },
    }
    return (
        {'role': 'assistant', 'content': '', 'tool_calls': [call_entry]},
        {'role': 'tool', 'tool_call_id': call_id, 'content': content},
    )


def rebuild_messages_from_activities(
    activities: Iterable[RebuildActivity],
    *,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Replay activities in immutable sequence order into provider messages."""
    messages: list[dict[str, Any]] = [{'role': 'system', 'content': system_prompt}]

    for activity in sorted(activities, key=lambda item: item.seq):
        kind = activity.kind
        details = activity.details

        if kind in {
            AgentSessionActivityKind.INPUT,
            AgentSessionActivityKind.OUTPUT,
        }:
            if activity.status != AgentSessionActivityStatus.SUCCEEDED or not isinstance(details, dict):
                continue
            content = details.get('content')
            if not isinstance(content, str):
                continue
            role = 'user' if kind == AgentSessionActivityKind.INPUT else 'assistant'
            messages.append({'role': role, 'content': content})

        elif kind == AgentSessionActivityKind.TOOL:
            expanded = _tool_messages(details, activity.status)
            if expanded is None:
                continue
            assistant_message, tool_message = expanded
            call_entry = assistant_message['tool_calls'][0]
            if messages and messages[-1]['role'] == 'assistant' and 'tool_calls' in messages[-1]:
                messages[-1]['tool_calls'].append(call_entry)
            elif messages and messages[-1]['role'] == 'assistant':
                messages[-1]['tool_calls'] = [call_entry]
                messages[-1].setdefault('content', '')
            else:
                messages.append(assistant_message)
            messages.append(tool_message)

    return messages


def rebuild_messages(session: AgentSession | UUID, *, system_prompt: str) -> list[dict[str, Any]]:
    """Replay persisted activities into an ordered OpenAI-style message list.

    Input/output details carry ``content``. A terminal succeeded or failed tool
    carries call metadata plus ``result`` and expands to the provider's
    assistant-tool pair. Container and boundary activities are omitted.
    """
    return rebuild_messages_from_activities(activities_for(session), system_prompt=system_prompt)
