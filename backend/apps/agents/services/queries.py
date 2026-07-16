# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Read models for agent config editor and catalog."""

from __future__ import annotations

from typing import Any

from apps.agents.models import Agent, AgentConfig, AgentConfigSource
from apps.agents.services.config_commands import suggest_identifier
from apps.agents.services.config_sync import config_source_label
from apps.keys.services.queries import list_referenceable_credentials
from apps.sessions.models import AgentSession
from django.urls import reverse
from libs.agent_spec import AgentConfigSpec, LLMSpec, list_examples
from libs.agent_spec.trigger_prompts import (
    DEFAULT_AGENT_TRIGGER_PROMPT,
    DEFAULT_QUEUE_TRIGGER_PROMPT,
    DEFAULT_SCHEDULE_TRIGGER_PROMPT,
)
from libs.providers.llm.anthropic_provider import AnthropicProvider
from libs.providers.llm.local_openai_provider import LocalOpenAIProvider
from libs.providers.llm.openai_provider import OpenAIProvider
from libs.providers.llm.registry import PROVIDERS
from libs.sources.registry import all_adapters
from libs.tools.context import ToolContext
from libs.tools.registry import all_tools

_DUMMY_CTX = ToolContext(
    spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
)

TRIGGER_KINDS = ['schedule', 'manual', 'agent', 'queue']


def _spec_summary_from_spec(spec: Any) -> dict[str, Any]:
    """Build helper dropdown summary from a parsed spec."""
    return {
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


def _empty_spec_summary() -> dict[str, Any]:
    return {'tools': [], 'triggers': [], 'queues': []}


SCHEMA_KEYS = [
    'schema_version',
    'description',
    'llm',
    'llm.provider',
    'llm.model',
    'llm.temperature',
    'llm.credential_ref',
    'system_prompt',
    'integrations',
    'integrations[]',
    'integrations[].id',
    'integrations[].type',
    'integrations[].credential_ref',
    'integrations[].config',
    'triggers',
    'triggers[]',
    'triggers[].name',
    'triggers[].kind',
    'triggers[].cron',
    'triggers[].queue',
    'triggers[].prompt',
    'triggers[].max_sessions',
    'tools',
    'tools[]',
    'tools[].id',
    'tools[].type',
    'tools[].integration',
    'tools[].credential_ref',
    'tools[].config',
    'tools[].allow',
    'tools[].deny',
    'queues',
    'queues[]',
    'queues[].id',
    'queues[].max_attempts',
    'queues[].sources',
    'queues[].sources[]',
    'queues[].sources[].id',
    'queues[].sources[].type',
    'queues[].sources[].integration',
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
                'functions': [{'name': fn.name, 'readonly': fn.readonly} for fn in tool.functions(_DUMMY_CTX)],
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
        'trigger_prompt_defaults': {
            'schedule': DEFAULT_SCHEDULE_TRIGGER_PROMPT,
            'queue': DEFAULT_QUEUE_TRIGGER_PROMPT,
            'agent': DEFAULT_AGENT_TRIGGER_PROMPT,
        },
        'schema_keys': SCHEMA_KEYS,
        'credentials': by_type,
        'examples': [{'slug': ex.slug, 'title': ex.title, 'description': ex.description} for ex in list_examples()],
    }


def list_config_history(agent: Agent, *, limit: int = 10) -> list[AgentConfig]:
    """Recent immutable config revisions, newest first."""
    return list(agent.configs.order_by('-fetched_at')[:limit])


def get_create_editor_context(
    user_id: int,
    *,
    initial_yaml: str,
    active_example: str = 'minimal',
    import_errors: list[Any] | None = None,
) -> dict[str, Any]:
    """Template context for the create-agent editor (same UI as edit, no agent yet)."""
    catalog = build_config_catalog(user_id)
    spec_summary = _empty_spec_summary()
    try:
        from apps.agents.services.config_validation import (
            ConfigValidationError,
            validate_agent_config_yaml,
        )

        spec_summary = _spec_summary_from_spec(validate_agent_config_yaml(initial_yaml))
    except ConfigValidationError:
        pass
    examples = list_examples()
    example = next((item for item in examples if item.slug == active_example), None)
    if active_example == 'minimal':
        suggested_name = 'New agent'
        suggested_identifier = suggest_identifier(user_id, 'agent')
    else:
        suggested_name = example.title if example else active_example.replace('-', ' ').title()
        suggested_identifier = suggest_identifier(user_id, active_example)
    page_data = {
        'initialYaml': initial_yaml,
        'catalog': catalog,
        'mode': 'create',
        'urls': {},
    }
    return {
        'is_create': True,
        'agent': None,
        'active_example': active_example,
        'examples': examples,
        'suggested_name': suggested_name,
        'suggested_identifier': suggested_identifier,
        'import_errors': import_errors or [],
        'spec_summary': spec_summary,
        'catalog': catalog,
        'page_data': page_data,
        'save_url': reverse('agent_create'),
        'mutate_url': reverse('agent_create_mutate'),
    }


def get_config_editor_context(agent: Agent, user_id: int) -> dict[str, Any]:
    """Template context for the config editor page."""
    config = agent.current_config
    read_only = agent.config_source == AgentConfigSource.DISK
    spec_yaml = ''
    spec_version = 0
    source_rev = '—'
    dirty = False
    if config is not None:
        spec_yaml = config.display_yaml()
        spec_version = config.spec_version
        source_rev = config.source_rev
        dirty = config.dirty

    pinned_sessions = AgentSession.objects.filter(
        agent=agent,
        status__in=['queued', 'running', 'waiting', 'paused'],
    ).count()

    catalog = build_config_catalog(user_id)
    spec_summary: dict[str, Any] = _empty_spec_summary()
    if config is not None:
        spec_summary = _spec_summary_from_spec(config.get_spec())
    page_data = {
        'initialYaml': spec_yaml,
        'catalog': catalog,
        'mode': 'edit',
        'readOnly': read_only,
        'urls': {},
    }
    return {
        'is_create': False,
        'agent': agent,
        'config': config,
        'spec_yaml': spec_yaml,
        'spec_version': spec_version,
        'source_rev': source_rev,
        'dirty': dirty,
        'read_only': read_only,
        'source_label': config_source_label(agent.config_source),
        'source_path': agent.source_path,
        'history': list_config_history(agent),
        'pinned_sessions': pinned_sessions,
        'catalog': catalog,
        'spec_summary': spec_summary,
        'page_data': page_data,
    }
