# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Registry mapping provider names to provider instances."""

from __future__ import annotations

from apps.agents.spec import LLMSpec
from apps.runner.errors import UnsupportedLLMProvider
from apps.runner.providers.anthropic_provider import AnthropicProvider
from apps.runner.providers.base import LLMProvider, ProviderFactory
from apps.runner.providers.local_openai_provider import LocalOpenAIProvider
from apps.runner.providers.openai_provider import OpenAIProvider
from apps.runner.providers.repeat_provider import RepeatProvider
from apps.runner.providers.spec import (
    AnthropicProviderConfig,
    OpenAIProviderConfig,
    RepeatProviderConfig,
)

PROVIDERS: dict[str, ProviderFactory] = {
    'openai': OpenAIProvider.from_spec(OpenAIProviderConfig()),
    'local_openai': LocalOpenAIProvider.from_spec(LocalOpenAIProvider.default_config()),
    'anthropic': AnthropicProvider.from_spec(AnthropicProviderConfig()),
    'repeat': RepeatProvider.from_spec(RepeatProviderConfig()),
}


def make_provider(llm: LLMSpec) -> LLMProvider:
    factory = PROVIDERS.get(llm.provider)
    if factory is None:
        raise UnsupportedLLMProvider(llm.provider)
    return factory(llm)
