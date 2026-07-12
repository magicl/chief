# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Functional usecase YAML loading and mock seeding helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from libs.clients.clickup.mock import MockClickUpClient
from libs.clients.gmail.mock import MockGmailClient
from libs.providers.llm.base import StreamResult
from libs.providers.llm.fake_provider import FakeProvider


@dataclass(frozen=True)
class UsecaseScenario:
    """Parsed functional usecase fixture data from a YAML document."""

    id: str
    prompt: str
    seed_gmail: Any
    seed_clickup: Any
    fake_responses: list[dict[str, Any]]
    expect: dict[str, Any]
    raw: dict[str, Any]


def load_usecase_scenario(path: str | Path) -> UsecaseScenario:
    """Load a functional usecase YAML document from disk."""
    raw = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('usecase YAML must be a mapping')

    scenario_id = raw.get('id')
    prompt = raw.get('prompt')
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError('usecase YAML requires non-empty string id')
    if not isinstance(prompt, str) or not prompt:
        raise ValueError('usecase YAML requires non-empty string prompt')

    return UsecaseScenario(
        id=scenario_id,
        prompt=prompt,
        seed_gmail=raw.get('seed_gmail') or [],
        seed_clickup=raw.get('seed_clickup') or {},
        fake_responses=list(raw.get('fake_responses') or []),
        expect=dict(raw.get('expect') or {}),
        raw=raw,
    )


def fake_provider_for_scenario(scenario: UsecaseScenario) -> FakeProvider:
    """Build a FakeProvider from the scenario's fake_responses records."""
    return fake_provider_for_responses(scenario.fake_responses)


def fake_provider_for_responses(responses: list[dict[str, Any]]) -> FakeProvider:
    """Build a FakeProvider from YAML records with content and tool_calls fields."""
    return FakeProvider.for_responses(
        [
            StreamResult(
                content=str(item.get('content') or ''),
                tool_calls=list(item.get('tool_calls') or []),
                latency_ms=item.get('latency_ms'),
            )
            for item in responses
        ],
    )


def build_mock_client_factories(
    scenario: UsecaseScenario,
) -> tuple[dict[str, Any], MockGmailClient, MockClickUpClient]:
    """Create seeded mock clients and factory mapping for SessionRunner client_factories."""
    gmail_client = MockGmailClient(token_supplier=lambda: None, config={})
    clickup_client = MockClickUpClient(token_supplier=lambda: None, config={})
    seed_gmail_client(gmail_client, scenario.seed_gmail)
    seed_clickup_client(clickup_client, scenario.seed_clickup)
    return (
        {
            'gmail': lambda **_kwargs: gmail_client,
            'clickup': lambda **_kwargs: clickup_client,
        },
        gmail_client,
        clickup_client,
    )


def seed_gmail_client(client: MockGmailClient, seed: Any) -> None:
    """Seed a MockGmailClient from a list or {messages: [...]} YAML shape."""
    messages = seed.get('messages', []) if isinstance(seed, Mapping) else seed
    if not isinstance(messages, list):
        raise ValueError('seed_gmail must be a list or mapping with messages')
    for record in messages:
        if not isinstance(record, Mapping):
            raise ValueError('seed_gmail messages must be mappings')
        message_id = str(record['id'])
        client.seed_message(
            message_id,
            labels=tuple(record.get('labels') or ()),
            message=dict(record.get('message') or {}),
            attachments=dict(record.get('attachments') or {}),
        )


def seed_clickup_client(client: MockClickUpClient, seed: Any) -> None:
    """Seed a MockClickUpClient from spaces, lists, and tasks YAML records."""
    if not isinstance(seed, Mapping):
        raise ValueError('seed_clickup must be a mapping')

    for space in seed.get('spaces') or []:
        if not isinstance(space, Mapping):
            raise ValueError('seed_clickup spaces must be mappings')
        client.seed_space(str(space['team_id']), dict(space.get('space') or space))

    for list_record in seed.get('lists') or []:
        if not isinstance(list_record, Mapping):
            raise ValueError('seed_clickup lists must be mappings')
        client.seed_list(str(list_record['space_id']), dict(list_record.get('list') or list_record))

    for task in seed.get('tasks') or []:
        if not isinstance(task, Mapping):
            raise ValueError('seed_clickup tasks must be mappings')
        client.seed_task(str(task['list_id']), dict(task.get('task') or task))
