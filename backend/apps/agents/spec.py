# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pydantic schema for agent configuration specs."""

from typing import Literal

from pydantic import BaseModel


class LLMSpec(BaseModel):
    provider: str  # e.g. "openai", "anthropic", "local_openai", "repeat"
    model: str
    temperature: float | None = None


class TriggerSpec(BaseModel):
    name: str
    kind: Literal['schedule', 'manual', 'agent']
    cron: str | None = None


class ToolPermission(BaseModel):
    tool: str
    allow: list[str] = ['*']
    deny: list[str] = []


class AgentConfigSpec(BaseModel):
    description: str | None = None
    llm: LLMSpec
    system_prompt: str
    triggers: list[TriggerSpec] = []
    tools: list[ToolPermission] = []
