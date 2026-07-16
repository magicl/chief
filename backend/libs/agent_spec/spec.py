# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pydantic schema for agent configuration specs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_CONFIG_SPEC_VERSION = 3

_INSTANCE_ID_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


class LLMSpec(BaseModel):
    provider: str  # e.g. "openai", "anthropic", "local_openai", "repeat"
    model: str
    temperature: float | None = None
    credential_ref: str | None = None


class TriggerSpec(BaseModel):
    name: str
    kind: Literal['schedule', 'manual', 'agent', 'queue']
    cron: str | None = None
    queue: str | None = None
    prompt: str | None = None
    max_sessions: int | None = None

    @model_validator(mode='before')
    @classmethod
    def _trigger_defaults(cls, data: Any) -> Any:
        """Apply per-kind defaults without conflating omitted and explicit null max_sessions."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        kind = out.get('kind')
        if kind == 'manual':
            out['max_sessions'] = None
        elif kind in ('schedule', 'queue') and 'max_sessions' not in out:
            out['max_sessions'] = 1
        return out

    @model_validator(mode='after')
    def _kind_specific_fields(self) -> TriggerSpec:
        if self.kind == 'schedule' and not self.cron:
            raise ValueError('cron is required when kind is schedule')
        if self.kind == 'queue' and not self.queue:
            raise ValueError('queue is required when kind is queue')
        if self.kind == 'manual':
            if self.prompt is not None and self.prompt.strip():
                raise ValueError('prompt must not be set when kind is manual')
        elif not (self.prompt and self.prompt.strip()):
            raise ValueError('prompt is required when kind is not manual')
        if self.max_sessions is not None and self.max_sessions < 1:
            raise ValueError('max_sessions must be >= 1 when set')
        return self


class IntegrationSpec(BaseModel):
    """Shared connection details referenced by tools and sources."""

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    credential_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ToolInstance(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    integration: str | None = None
    credential_ref: str | None = None
    config: dict[str, Any] = {}  # non-secret per-instance addressing
    allow: list[str] = ['*']
    deny: list[str] = []


class SourceSpec(BaseModel):
    """YAML fragment for one source nested under a queue."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    adapter_type: str = Field(validation_alias='type', serialization_alias='type')
    integration: str | None = None
    credential_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class QueueSpec(BaseModel):
    """YAML fragment for one agent-scoped queue and optional nested sources."""

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    max_attempts: int = Field(default=3, ge=1)
    min_hold_seconds: int = Field(default=60, ge=1)
    early_release_seconds: int = Field(default=300, ge=1)
    long_hold_seconds: int = Field(default=3600, ge=1)
    sources: list[SourceSpec] = []

    @model_validator(mode='after')
    def _hold_seconds_ordered(self) -> QueueSpec:
        """Require min_hold <= early_release <= long_hold."""
        if self.early_release_seconds < self.min_hold_seconds:
            raise ValueError('early_release_seconds must be >= min_hold_seconds')
        if self.long_hold_seconds < self.early_release_seconds:
            raise ValueError('long_hold_seconds must be >= early_release_seconds')
        return self


def _integration_map(raw_integrations: Any) -> dict[str, dict[str, Any]]:
    """Index integration dicts by id; reject duplicates."""
    if not raw_integrations:
        return {}
    if not isinstance(raw_integrations, list):
        raise ValueError('integrations must be a list')
    by_id: dict[str, dict[str, Any]] = {}
    for entry in raw_integrations:
        if not isinstance(entry, dict):
            raise ValueError('integration entries must be mappings')
        integ_id = entry.get('id')
        if not isinstance(integ_id, str) or not integ_id:
            raise ValueError('integration id is required')
        if integ_id in by_id:
            raise ValueError(f'duplicate integration id {integ_id!r}')
        by_id[integ_id] = entry
    return by_id


def _apply_integration(entry: dict[str, Any], integrations: dict[str, dict[str, Any]], *, kind: str) -> dict[str, Any]:
    """Fill type / credential_ref / config from a named integration when referenced."""
    out = dict(entry)
    ref = out.get('integration')
    if not ref:
        return out
    if not isinstance(ref, str):
        raise ValueError(f'{kind} integration must be a string id')
    integ = integrations.get(ref)
    if integ is None:
        raise ValueError(f'{kind} references unknown integration {ref!r}')
    integ_type = integ.get('type')
    if out.get('type') is None:
        out['type'] = integ_type
    elif out.get('type') != integ_type:
        raise ValueError(
            f"{kind} type {out.get('type')!r} conflicts with integration {ref!r} type {integ_type!r}",
        )
    # Distinguish omitted credential_ref from explicit null (opt out of inherited cred).
    if 'credential_ref' not in out and integ.get('credential_ref') is not None:
        out['credential_ref'] = integ.get('credential_ref')
    merged = dict(integ.get('config') or {})
    merged.update(out.get('config') or {})
    out['config'] = merged
    return out


class SkillSpec(BaseModel):
    """Named prompt block loadable on demand via the load_skill tool."""

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    description: str = Field(min_length=1)
    content: str = Field(min_length=1)


class AgentConfigSpec(BaseModel):
    schema_version: Literal[3] = 3
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    integrations: list[IntegrationSpec] = []
    triggers: list[TriggerSpec] = []
    tools: list[ToolInstance] = []
    queues: list[QueueSpec] = []
    skills: list[SkillSpec] = []

    @model_validator(mode='before')
    @classmethod
    def _resolve_integrations(cls, data: Any) -> Any:
        """Expand ``integration:`` refs on tools and sources before field validation."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        integrations = _integration_map(out.get('integrations'))
        tools_in = out.get('tools') or []
        out['tools'] = [
            _apply_integration(dict(tool), integrations, kind='tool') if isinstance(tool, dict) else tool
            for tool in tools_in
        ]
        queues_out: list[Any] = []
        for queue in out.get('queues') or []:
            if not isinstance(queue, dict):
                queues_out.append(queue)
                continue
            queue_out = dict(queue)
            sources_in = queue_out.get('sources') or []
            queue_out['sources'] = [
                _apply_integration(dict(source), integrations, kind='source') if isinstance(source, dict) else source
                for source in sources_in
            ]
            queues_out.append(queue_out)
        out['queues'] = queues_out
        return out

    @model_validator(mode='after')
    def _unique_instance_ids(self) -> AgentConfigSpec:
        """Reject duplicate tool, queue, and nested source ids within one spec."""
        tool_ids = [t.id for t in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError('duplicate tool instance id')
        queue_ids = [q.id for q in self.queues]
        if len(queue_ids) != len(set(queue_ids)):
            raise ValueError('duplicate queue id')
        for queue in self.queues:
            source_ids = [s.id for s in queue.sources]
            if len(source_ids) != len(set(source_ids)):
                raise ValueError(f'duplicate source id in queue {queue.id!r}')
        skill_ids = [s.id for s in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError('duplicate skill id')
        return self

    @model_validator(mode='after')
    def _trigger_queue_refs(self) -> AgentConfigSpec:
        """Ensure queue triggers reference ids declared in queues[]."""
        queue_ids = {queue.id for queue in self.queues}
        for trigger in self.triggers:
            if trigger.kind == 'queue' and trigger.queue not in queue_ids:
                raise ValueError(
                    f"trigger {trigger.name!r} references unknown queue {trigger.queue!r}",
                )
        return self
