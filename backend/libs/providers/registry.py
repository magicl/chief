# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Registry mapping provider names to provider instances."""

from __future__ import annotations

from libs.providers.anthropic_provider import AnthropicProvider
from libs.providers.base import LLMProvider, ProviderFactory
from libs.providers.errors import UnsupportedLLMProvider
from libs.providers.local_openai_provider import LocalOpenAIProvider
from libs.providers.openai_provider import OpenAIProvider
from libs.providers.repeat_provider import RepeatProvider
from libs.providers.spec import (
    AnthropicProviderConfig,
    OpenAIProviderConfig,
    RepeatProviderConfig,
)
from libs.providers.types import ProviderLLMConfig

PROVIDERS: dict[str, ProviderFactory] = {
    'openai': OpenAIProvider.from_spec(OpenAIProviderConfig()),
    'local_openai': LocalOpenAIProvider.from_spec(LocalOpenAIProvider.default_config()),
    'anthropic': AnthropicProvider.from_spec(AnthropicProviderConfig()),
    'repeat': RepeatProvider.from_spec(RepeatProviderConfig()),
}


def make_provider(llm: ProviderLLMConfig) -> LLMProvider:
    factory = PROVIDERS.get(llm.provider)
    if factory is None:
        raise UnsupportedLLMProvider(llm.provider)
    return factory(llm)
