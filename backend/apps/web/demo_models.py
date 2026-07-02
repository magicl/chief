# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dashboard-only catalog of demo agent models (one create button per model)."""

from __future__ import annotations

from dataclasses import dataclass

from libs.providers.anthropic_provider import AnthropicProvider
from libs.providers.local_openai_provider import LocalOpenAIProvider
from libs.providers.openai_provider import OpenAIProvider


@dataclass(frozen=True)
class DemoModelOption:
    provider: str
    model: str
    label: str


def list_demo_models() -> list[DemoModelOption]:
    """Models exposed as dashboard create-agent buttons."""
    options: list[DemoModelOption] = []
    for model_id in OpenAIProvider.models:
        options.append(DemoModelOption('openai', model_id, f'OpenAI · {model_id}'))
    for model_id in sorted(AnthropicProvider.models):
        # Skip dot-alias entries (same pricing as dashed id).
        if '.' in model_id:
            continue
        options.append(DemoModelOption('anthropic', model_id, f'Anthropic · {model_id}'))
    for model_id in LocalOpenAIProvider.models:
        options.append(DemoModelOption('local_openai', model_id, f'Local · {model_id}'))
    return options
