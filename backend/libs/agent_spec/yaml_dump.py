# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Stable YAML serialization for ``AgentConfigSpec`` editor display."""

from __future__ import annotations

from typing import Any

from libs.agent_spec import AgentConfigSpec
from libs.file.yaml_dump import dump_editable_yaml


def _collapse_integration_fields(entry: dict[str, Any], integrations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Drop fields that match the referenced integration so dumps stay DRY."""
    ref = entry.get('integration')
    if not isinstance(ref, str) or ref not in integrations:
        return entry
    integ = integrations[ref]
    out = dict(entry)
    if out.get('type') == integ.get('type'):
        out.pop('type', None)
    if 'credential_ref' in out and out.get('credential_ref') == integ.get('credential_ref'):
        out.pop('credential_ref', None)
    integ_config = integ.get('config') or {}
    config = dict(out.get('config') or {})
    for key, value in list(config.items()):
        if integ_config.get(key) == value:
            del config[key]
    if config:
        out['config'] = config
    else:
        out.pop('config', None)
    return out


def _collapse_integrations_for_dump(data: dict[str, Any]) -> dict[str, Any]:
    """Rewrite resolved tools/sources back to integration-relative form for YAML."""
    out = dict(data)
    integrations = {
        item['id']: item for item in (out.get('integrations') or []) if isinstance(item, dict) and 'id' in item
    }
    if not integrations:
        return out
    out['tools'] = [
        _collapse_integration_fields(dict(tool), integrations) if isinstance(tool, dict) else tool
        for tool in (out.get('tools') or [])
    ]
    queues_out: list[Any] = []
    for queue in out.get('queues') or []:
        if not isinstance(queue, dict):
            queues_out.append(queue)
            continue
        queue_out = dict(queue)
        queue_out['sources'] = [
            _collapse_integration_fields(dict(source), integrations) if isinstance(source, dict) else source
            for source in (queue_out.get('sources') or [])
        ]
        queues_out.append(queue_out)
    out['queues'] = queues_out
    return out


def _restore_explicit_null_credential_refs(spec: AgentConfigSpec, data: dict[str, Any]) -> None:
    """Re-insert ``credential_ref: null`` when a tool/source opted out of an integration cred."""
    integ_creds = {item.id: item.credential_ref for item in spec.integrations}
    for index, tool in enumerate(spec.tools):
        if not tool.integration:
            continue
        if tool.credential_ref is not None:
            continue
        if integ_creds.get(tool.integration) is None:
            continue
        data['tools'][index]['credential_ref'] = None
    for q_index, queue in enumerate(spec.queues):
        for s_index, source in enumerate(queue.sources):
            if not source.integration:
                continue
            if source.credential_ref is not None:
                continue
            if integ_creds.get(source.integration) is None:
                continue
            data['queues'][q_index]['sources'][s_index]['credential_ref'] = None


def dump_agent_config_spec(spec: AgentConfigSpec) -> str:
    """Dump spec to YAML with block style for multiline strings."""
    data = spec.model_dump(mode='json', exclude_none=True, by_alias=True)
    _restore_explicit_null_credential_refs(spec, data)
    data = _collapse_integrations_for_dump(data)
    return dump_editable_yaml(
        data,
        sort_keys=True,
        width=120,
    )
