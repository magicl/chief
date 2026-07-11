# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Registry mapping provider names to provider instances."""

from __future__ import annotations

from libs.providers.llm.anthropic_provider import AnthropicProvider
from libs.providers.llm.base import LLMProvider, ProviderFactory
from libs.providers.llm.errors import UnsupportedLLMProvider
from libs.providers.llm.local_openai_provider import LocalOpenAIProvider
from libs.providers.llm.openai_provider import OpenAIProvider
from libs.providers.llm.repeat_provider import RepeatProvider
from libs.providers.llm.spec import (
    AnthropicProviderConfig,
    OpenAIProviderConfig,
    RepeatProviderConfig,
)
from libs.providers.llm.types import ProviderLLMConfig

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
