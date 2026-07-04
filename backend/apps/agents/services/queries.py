# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read models for agent config editor and catalog."""

from __future__ import annotations

import json
from typing import Any

from apps.agents.models import Agent, AgentConfig
from apps.agents.services.config_sync import config_source_label
from apps.keys.services.queries import list_referenceable_credentials
from apps.sessions.models import AgentSession
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import list_examples
from libs.providers.anthropic_provider import AnthropicProvider
from libs.providers.local_openai_provider import LocalOpenAIProvider
from libs.providers.openai_provider import OpenAIProvider
from libs.providers.registry import PROVIDERS
from libs.sources.registry import all_adapters
from libs.tools.registry import all_tools

TRIGGER_KINDS = ['schedule', 'manual', 'agent']

SCHEMA_KEYS = [
    'schema_version',
    'description',
    'llm',
    'llm.provider',
    'llm.model',
    'llm.temperature',
    'llm.credential_ref',
    'system_prompt',
    'triggers',
    'triggers[]',
    'triggers[].name',
    'triggers[].kind',
    'triggers[].cron',
    'tools',
    'tools[]',
    'tools[].id',
    'tools[].type',
    'tools[].credential_ref',
    'tools[].allow',
    'tools[].deny',
    'queues',
    'queues[]',
    'queues[].id',
    'queues[].max_attempts',
    'queues[].sources',
    'queues[].sources[]',
    'queues[].sources[].id',
    'queues[].sources[].adapter_type',
    'queues[].sources[].credential_ref',
    'queues[].sources[].config',
]


def _provider_catalog() -> list[dict[str, Any]]:
    """Build provider/model options for the config editor catalog."""
    items: list[dict[str, Any]] = []
    for name in sorted(PROVIDERS):
        if name == 'openai':
            models = sorted(OpenAIProvider.models.keys())
        elif name == 'anthropic':
            models = sorted(k for k in AnthropicProvider.models if '.' not in k)
        elif name == 'local_openai':
            models = sorted(LocalOpenAIProvider.models.keys())
        else:
            models = []
        items.append({'provider': name, 'models': models})
    return items


def _tool_catalog() -> list[dict[str, Any]]:
    """Build registered tool types for helper dropdowns."""
    items: list[dict[str, Any]] = []
    for name, tool in sorted(all_tools().items()):
        items.append(
            {
                'type': name,
                'credential_type': getattr(tool, 'credential_type', None),
                'functions': [fn.name for fn in tool.functions()],
            },
        )
    return items


def _adapter_catalog() -> list[dict[str, Any]]:
    """Build registered source adapter types for helper dropdowns."""
    items: list[dict[str, Any]] = []
    for name, adapter in sorted(all_adapters().items()):
        items.append(
            {
                'type': name,
                'credential_type': adapter.credential_type,
            },
        )
    return items


def build_config_catalog(user_id: int) -> dict[str, Any]:
    """Payload for YAML autocomplete and helper dropdowns."""
    creds = list_referenceable_credentials(user_id)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in creds:
        by_type.setdefault(row.type, []).append(
            {'name': row.name, 'is_set': row.is_set},
        )
    return {
        'providers': _provider_catalog(),
        'tool_types': _tool_catalog(),
        'adapter_types': _adapter_catalog(),
        'trigger_kinds': TRIGGER_KINDS,
        'schema_keys': SCHEMA_KEYS,
        'credentials': by_type,
        'examples': [{'slug': ex.slug, 'title': ex.title, 'description': ex.description} for ex in list_examples()],
    }


def list_config_history(agent: Agent, *, limit: int = 10) -> list[AgentConfig]:
    """Recent immutable config revisions, newest first."""
    return list(agent.configs.order_by('-fetched_at')[:limit])


def get_config_editor_context(agent: Agent, user_id: int) -> dict[str, Any]:
    """Template context for the config editor page."""
    config = agent.current_config
    spec_yaml = ''
    spec_version = 0
    source_rev = '—'
    dirty = False
    if config is not None:
        spec_yaml = dump_agent_config_spec(config.get_spec())
        spec_version = config.spec_version
        source_rev = config.source_rev
        dirty = config.dirty

    pinned_sessions = AgentSession.objects.filter(
        agent=agent,
        status__in=['queued', 'running', 'waiting', 'paused'],
    ).count()

    catalog = build_config_catalog(user_id)
    spec_summary: dict[str, Any] = {'tools': [], 'triggers': [], 'queues': []}
    if config is not None:
        spec = config.get_spec()
        spec_summary = {
            'tools': [{'id': t.id, 'type': t.type} for t in spec.tools],
            'triggers': [{'name': t.name, 'kind': t.kind} for t in spec.triggers],
            'queues': [
                {
                    'id': q.id,
                    'sources': [{'id': s.id, 'type': s.adapter_type} for s in q.sources],
                }
                for q in spec.queues
            ],
        }
    return {
        'agent': agent,
        'config': config,
        'spec_yaml': spec_yaml,
        'spec_version': spec_version,
        'source_rev': source_rev,
        'dirty': dirty,
        'source_label': config_source_label(agent.config_source),
        'history': list_config_history(agent),
        'pinned_sessions': pinned_sessions,
        'catalog': catalog,
        'catalog_json': json.dumps(catalog),
        'spec_summary': spec_summary,
        'page_data_json': json.dumps(
            {
                'initialYaml': spec_yaml,
                'catalog': catalog,
            },
        ),
    }
