# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pydantic schema for agent configuration specs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_CONFIG_SPEC_VERSION = 2

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


class ToolInstance(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    credential_ref: str | None = None
    allow: list[str] = ['*']
    deny: list[str] = []


class SourceSpec(BaseModel):
    """YAML fragment for one source nested under a queue."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    adapter_type: str = Field(validation_alias='type')
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


class AgentConfigSpec(BaseModel):
    schema_version: Literal[2] = 2
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    triggers: list[TriggerSpec] = []
    tools: list[ToolInstance] = []
    queues: list[QueueSpec] = []

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
