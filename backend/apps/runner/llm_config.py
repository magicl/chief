# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Map agent spec LLM config to provider-local types."""

from __future__ import annotations

from apps.agents.spec import LLMSpec
from libs.providers.types import ProviderLLMConfig


def provider_config_from_spec(llm: LLMSpec) -> ProviderLLMConfig:
    return ProviderLLMConfig(
        provider=llm.provider,
        model=llm.model,
        temperature=llm.temperature,
    )
