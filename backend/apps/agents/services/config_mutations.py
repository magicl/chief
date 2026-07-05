# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Server-side YAML mutations for config editor helpers."""

from __future__ import annotations

from typing import Any

from apps.agents.services.config_validation import (
    validate_agent_config_spec,
    validate_agent_config_yaml,
)
from libs.agent_spec import (
    LLMSpec,
    QueueSpec,
    SourceSpec,
    ToolInstance,
    TriggerSpec,
)
from libs.agent_spec.yaml_dump import dump_agent_config_spec


class ConfigMutationError(ValueError):
    """Helper mutation could not be applied."""


def apply_config_mutation(raw: str, mutation: dict[str, Any]) -> str:
    """Parse *raw*, apply *mutation*, re-dump YAML (no persist)."""
    spec = validate_agent_config_yaml(raw)
    action = mutation.get('action')
    if not action:
        raise ConfigMutationError('action required')

    if action == 'set_llm':
        spec = spec.model_copy(
            update={
                'llm': LLMSpec(
                    provider=mutation['provider'],
                    model=mutation['model'],
                    temperature=mutation.get('temperature'),
                    credential_ref=mutation.get('credential_ref') or None,
                ),
            },
        )
    elif action == 'set_system_prompt':
        spec = spec.model_copy(update={'system_prompt': mutation['system_prompt']})
    elif action == 'add_tool':
        inst = ToolInstance(
            id=mutation['id'],
            type=mutation['type'],
            credential_ref=mutation.get('credential_ref') or None,
            allow=mutation.get('allow') or ['*'],
            deny=mutation.get('deny') or [],
        )
        spec = spec.model_copy(update={'tools': [*spec.tools, inst]})
    elif action == 'remove_tool':
        tool_id = mutation['id']
        tools = [t for t in spec.tools if t.id != tool_id]
        if len(tools) == len(spec.tools):
            raise ConfigMutationError(f'Unknown tool instance {tool_id!r}')
        spec = spec.model_copy(update={'tools': tools})
    elif action == 'add_trigger':
        trig = TriggerSpec(
            name=mutation['name'],
            kind=mutation['kind'],
            cron=mutation.get('cron'),
            queue=mutation.get('queue'),
            max_sessions=int(mutation.get('max_sessions') or 1),
        )
        spec = spec.model_copy(update={'triggers': [*spec.triggers, trig]})
    elif action == 'remove_trigger':
        name = mutation['name']
        triggers = [t for t in spec.triggers if t.name != name]
        if len(triggers) == len(spec.triggers):
            raise ConfigMutationError(f'Unknown trigger {name!r}')
        spec = spec.model_copy(update={'triggers': triggers})
    elif action == 'add_queue':
        queue = QueueSpec(
            id=mutation['id'],
            max_attempts=mutation.get('max_attempts', 3),
            min_hold_seconds=mutation.get('min_hold_seconds', 60),
            early_release_seconds=mutation.get('early_release_seconds', 300),
            long_hold_seconds=mutation.get('long_hold_seconds', 3600),
            sources=[],
        )
        spec = spec.model_copy(update={'queues': [*spec.queues, queue]})
    elif action == 'remove_queue':
        queue_id = mutation['id']
        queues = [q for q in spec.queues if q.id != queue_id]
        if len(queues) == len(spec.queues):
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        spec = spec.model_copy(update={'queues': queues})
    elif action == 'add_source':
        queue_id = mutation['queue_id']
        source = SourceSpec(
            id=mutation['id'],
            adapter_type=mutation['type'],
            credential_ref=mutation.get('credential_ref') or None,
            config=mutation.get('config') or {},
        )
        queues = []
        found = False
        for queue in spec.queues:
            if queue.id == queue_id:
                queues.append(queue.model_copy(update={'sources': [*queue.sources, source]}))
                found = True
            else:
                queues.append(queue)
        if not found:
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        spec = spec.model_copy(update={'queues': queues})
    elif action == 'remove_source':
        queue_id = mutation['queue_id']
        source_id = mutation['id']
        queues = []
        found = False
        for queue in spec.queues:
            if queue.id == queue_id:
                sources = [s for s in queue.sources if s.id != source_id]
                if len(sources) == len(queue.sources):
                    raise ConfigMutationError(f'Unknown source {source_id!r} in queue {queue_id!r}')
                queues.append(queue.model_copy(update={'sources': sources}))
                found = True
            else:
                queues.append(queue)
        if not found:
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        spec = spec.model_copy(update={'queues': queues})
    else:
        raise ConfigMutationError(f'Unknown action {action!r}')

    validate_agent_config_spec(spec)
    return dump_agent_config_spec(spec)
