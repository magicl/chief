# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""OpenAI-compatible local LLM provider with power-based cost estimation."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import ClassVar

from apps.agents.spec import LLMSpec
from apps.runner.providers.base import ModelPricing, Usage
from apps.runner.providers.openai_provider import OpenAIProvider
from apps.runner.providers.spec import LocalOpenAIProviderConfig
from pydantic import BaseModel


class LocalOpenAIProvider(OpenAIProvider):
    """Uses OpenAI wire format against a local OpenAI-compatible server."""

    # Known local models; cost is power-based (see compute_cost_usd), not per-token.
    models: ClassVar[dict[str, ModelPricing]] = {
        'llama3.2': ModelPricing(
            input_per_million=Decimal('0'),
            output_per_million=Decimal('0'),
        ),
    }

    @classmethod
    def default_config(cls) -> LocalOpenAIProviderConfig:
        return LocalOpenAIProviderConfig(
            hostname=os.environ.get('LOCAL_OPENAI_HOST', 'localhost:11434'),
            power_watts=Decimal(os.environ.get('LOCAL_LLM_POWER_WATTS', '300')),
            power_usd_per_kwh=Decimal(os.environ.get('LOCAL_LLM_POWER_USD_PER_KWH', '0.15')),
        )

    @classmethod
    def _from_spec(cls, provider_config: BaseModel, llm: LLMSpec) -> LocalOpenAIProvider:
        config = LocalOpenAIProviderConfig.model_validate(provider_config.model_dump())
        return cls(
            llm.model,
            hostname=config.hostname,
            temperature=llm.temperature,
            power_watts=config.power_watts,
            power_usd_per_kwh=config.power_usd_per_kwh,
        )

    def __init__(
        self,
        model: str,
        *,
        hostname: str,
        temperature: float | None = None,
        power_watts: Decimal | None = None,
        power_usd_per_kwh: Decimal | None = None,
    ) -> None:
        base_url = hostname if '://' in hostname else f'http://{hostname}/v1'
        super().__init__(
            model,
            temperature=temperature,
            base_url=base_url,
            api_key=os.environ.get('LOCAL_OPENAI_API_KEY', 'local'),
        )
        self._power_watts = power_watts or Decimal(os.environ.get('LOCAL_LLM_POWER_WATTS', '300'))
        self._power_usd_per_kwh = power_usd_per_kwh or Decimal(os.environ.get('LOCAL_LLM_POWER_USD_PER_KWH', '0.15'))

    def compute_cost_usd(self, usage: Usage, *, latency_ms: int | None = None) -> Decimal | None:
        """Estimate USD cost from GPU/server power draw and wall-clock latency."""
        del usage
        if latency_ms is None:
            return None
        hours = Decimal(latency_ms) / Decimal(3_600_000)
        kw = self._power_watts / Decimal(1000)
        cost = hours * kw * self._power_usd_per_kwh
        return cost.quantize(Decimal('0.000001'))
