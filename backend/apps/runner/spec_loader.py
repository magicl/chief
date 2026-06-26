# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Load AgentConfigSpec from JSON or YAML text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from apps.agents.spec import AgentConfigSpec, LLMSpec, ToolPermission, TriggerSpec


def load_agent_config_spec(raw: str) -> AgentConfigSpec:
    """Parse *raw* as JSON or YAML into an ``AgentConfigSpec``."""
    data = _parse_structured_text(raw)
    return AgentConfigSpec.model_validate(data)


def load_agent_config_spec_file(path: str | Path) -> AgentConfigSpec:
    text = Path(path).read_text(encoding='utf-8')
    return load_agent_config_spec(text)


def build_agent_config_spec(
    *,
    provider: str,
    model: str,
    temperature: float | None = None,
    system_prompt: str = 'You are a helpful assistant.',
    tools: list[ToolPermission] | None = None,
) -> AgentConfigSpec:
    return AgentConfigSpec(
        llm=LLMSpec(provider=provider, model=model, temperature=temperature),
        system_prompt=system_prompt,
        triggers=[TriggerSpec(name='manual', kind='manual')],
        tools=tools or [ToolPermission(tool='clock', allow=['now'])],
    )


def _parse_structured_text(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        raise ValueError('Spec text is empty')
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return yaml.safe_load(stripped)
