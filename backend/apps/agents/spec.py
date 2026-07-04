# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pydantic schema for agent configuration specs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

AGENT_CONFIG_SPEC_VERSION = 1

_INSTANCE_ID_RE = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


class LLMSpec(BaseModel):
    provider: str  # e.g. "openai", "anthropic", "local_openai", "repeat"
    model: str
    temperature: float | None = None
    credential_ref: str | None = None


class TriggerSpec(BaseModel):
    name: str
    kind: Literal['schedule', 'manual', 'agent']
    cron: str | None = None


class ToolInstance(BaseModel):
    id: str = Field(pattern=_INSTANCE_ID_RE.pattern)
    type: str
    credential_ref: str | None = None
    allow: list[str] = ['*']
    deny: list[str] = []


class AgentConfigSpec(BaseModel):
    schema_version: Literal[1] = 1
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    triggers: list[TriggerSpec] = []
    tools: list[ToolInstance] = []

    @model_validator(mode='after')
    def _unique_instance_ids(self) -> AgentConfigSpec:
        ids = [t.id for t in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate tool instance id')
        return self


def load_spec(raw: dict[str, Any], *, stored_version: int | None = None) -> AgentConfigSpec:
    from apps.agents.spec_migrations import load_spec_dict

    return AgentConfigSpec.model_validate(load_spec_dict(raw, stored_version=stored_version))
