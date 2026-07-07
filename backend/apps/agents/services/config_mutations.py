# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Server-side YAML mutations for config editor helpers."""

from __future__ import annotations

from typing import Any

from apps.agents.services.config_validation import validate_agent_config_spec
from libs.agent_spec import load_spec
from libs.agent_spec.trigger_prompts import default_trigger_prompt
from libs.agent_spec.yaml_roundtrip import (
    dump_yaml_document,
    load_yaml_document,
    plain_dict,
)
from ruamel.yaml.comments import CommentedMap


class ConfigMutationError(ValueError):
    """Helper mutation could not be applied."""


def _tool_entry(mutation: dict[str, Any]) -> CommentedMap:
    """Build a tool-instance mapping for helper insertions."""
    entry: CommentedMap = CommentedMap()
    entry['id'] = mutation['id']
    entry['type'] = mutation['type']
    if mutation.get('credential_ref'):
        entry['credential_ref'] = mutation['credential_ref']
    allow = mutation.get('allow') or ['*']
    entry['allow'] = list(allow)
    deny = mutation.get('deny') or []
    if deny:
        entry['deny'] = list(deny)
    return entry


def _trigger_entry(mutation: dict[str, Any]) -> CommentedMap:
    """Build a trigger mapping for helper insertions."""
    kind = mutation['kind']
    prompt = mutation.get('prompt')
    if kind != 'manual' and not (prompt and str(prompt).strip()):
        prompt = default_trigger_prompt(kind)
    entry: CommentedMap = CommentedMap()
    entry['name'] = mutation['name']
    entry['kind'] = kind
    if mutation.get('cron'):
        entry['cron'] = mutation['cron']
    if mutation.get('queue'):
        entry['queue'] = mutation['queue']
    if prompt:
        entry['prompt'] = prompt
    max_sessions = mutation.get('max_sessions')
    if max_sessions is not None:
        entry['max_sessions'] = int(max_sessions)
    return entry


def _queue_entry(mutation: dict[str, Any]) -> CommentedMap:
    """Build a queue mapping for helper insertions."""
    entry: CommentedMap = CommentedMap()
    entry['id'] = mutation['id']
    entry['max_attempts'] = mutation.get('max_attempts', 3)
    entry['min_hold_seconds'] = mutation.get('min_hold_seconds', 60)
    entry['early_release_seconds'] = mutation.get('early_release_seconds', 300)
    entry['long_hold_seconds'] = mutation.get('long_hold_seconds', 3600)
    return entry


def _source_entry(mutation: dict[str, Any]) -> CommentedMap:
    """Build a source mapping for helper insertions."""
    entry: CommentedMap = CommentedMap()
    entry['id'] = mutation['id']
    entry['type'] = mutation['type']
    if mutation.get('credential_ref'):
        entry['credential_ref'] = mutation['credential_ref']
    config = mutation.get('config') or {}
    if config:
        entry['config'] = CommentedMap(config)
    return entry


def _apply_mutation_to_doc(doc: CommentedMap, mutation: dict[str, Any]) -> None:
    """Apply one helper mutation to an in-memory YAML document."""
    action = mutation.get('action')
    if not action:
        raise ConfigMutationError('action required')

    if action == 'set_llm':
        llm: CommentedMap = CommentedMap()
        llm['provider'] = mutation['provider']
        llm['model'] = mutation['model']
        if mutation.get('temperature') is not None:
            llm['temperature'] = mutation['temperature']
        if mutation.get('credential_ref'):
            llm['credential_ref'] = mutation['credential_ref']
        doc['llm'] = llm
        return

    if action == 'set_system_prompt':
        doc['system_prompt'] = mutation['system_prompt']
        return

    if action == 'add_tool':
        tools = doc.setdefault('tools', [])
        tools.append(_tool_entry(mutation))
        return

    if action == 'remove_tool':
        tool_id = mutation['id']
        tools = doc.get('tools', [])
        filtered = [item for item in tools if item.get('id') != tool_id]
        if len(filtered) == len(tools):
            raise ConfigMutationError(f'Unknown tool instance {tool_id!r}')
        doc['tools'] = filtered
        return

    if action == 'add_trigger':
        triggers = doc.setdefault('triggers', [])
        triggers.append(_trigger_entry(mutation))
        return

    if action == 'remove_trigger':
        name = mutation['name']
        triggers = doc.get('triggers', [])
        filtered = [item for item in triggers if item.get('name') != name]
        if len(filtered) == len(triggers):
            raise ConfigMutationError(f'Unknown trigger {name!r}')
        doc['triggers'] = filtered
        return

    if action == 'add_queue':
        queues = doc.setdefault('queues', [])
        queues.append(_queue_entry(mutation))
        return

    if action == 'remove_queue':
        queue_id = mutation['id']
        queues = doc.get('queues', [])
        filtered = [item for item in queues if item.get('id') != queue_id]
        if len(filtered) == len(queues):
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        doc['queues'] = filtered
        return

    if action == 'add_source':
        queue_id = mutation['queue_id']
        queues = doc.get('queues', [])
        found = False
        for queue in queues:
            if queue.get('id') == queue_id:
                sources = queue.setdefault('sources', [])
                sources.append(_source_entry(mutation))
                found = True
                break
        if not found:
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        return

    if action == 'remove_source':
        queue_id = mutation['queue_id']
        source_id = mutation['id']
        queues = doc.get('queues', [])
        found = False
        for queue in queues:
            if queue.get('id') != queue_id:
                continue
            sources = queue.get('sources', [])
            filtered = [item for item in sources if item.get('id') != source_id]
            if len(filtered) == len(sources):
                raise ConfigMutationError(f'Unknown source {source_id!r} in queue {queue_id!r}')
            queue['sources'] = filtered
            found = True
            break
        if not found:
            raise ConfigMutationError(f'Unknown queue {queue_id!r}')
        return

    raise ConfigMutationError(f'Unknown action {action!r}')


def apply_config_mutation(raw: str, mutation: dict[str, Any]) -> str:
    """Parse *raw*, apply *mutation*, and return YAML preserving comments."""
    doc = load_yaml_document(raw)
    _apply_mutation_to_doc(doc, mutation)
    spec = load_spec(plain_dict(doc))
    validate_agent_config_spec(spec)
    return dump_yaml_document(doc)
