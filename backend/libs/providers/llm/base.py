# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from typing import Any, ClassVar

from libs.providers.llm.types import ProviderLLMConfig
from libs.tools.schema import ToolDefinition
from pydantic import BaseModel

ProviderFactory = Callable[[ProviderLLMConfig], 'LLMProvider']


class ModelPricing(BaseModel):
    """USD per 1M tokens; cached rates optional (discounted vs regular input)."""

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    cache_creation_input_per_million: Decimal | None = None
    supports_temperature: bool = True


class Usage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class ProviderError:
    message: str
    code: str


@dataclass
class StreamResult:
    content: str = ''
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage | None = None
    error: ProviderError | None = None
    latency_ms: int | None = None


@dataclass
class Delta:
    kind: str  # 'text' | 'tool_call'
    text: str | None = None
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments_delta: str | None = None


class LLMProvider(ABC):
    """Each concrete provider owns its model catalog and cost computation."""

    models: ClassVar[dict[str, ModelPricing]] = {}

    @classmethod
    def from_spec(cls, provider_config: BaseModel) -> ProviderFactory:
        return partial(cls._from_spec, provider_config)

    @classmethod
    @abstractmethod
    def _from_spec(cls, provider_config: BaseModel, llm: ProviderLLMConfig) -> LLMProvider:
        raise NotImplementedError

    @abstractmethod
    def format_tools(self, definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Translate Chief tool definitions to this provider's wire format."""
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> Iterator[Delta]:
        raise NotImplementedError

    @abstractmethod
    def collect(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> StreamResult:
        """Run one completion and return assembled output + usage (or error)."""
        raise NotImplementedError

    def compute_cost_usd(self, usage: Usage, *, latency_ms: int | None = None) -> Decimal | None:
        """Return USD cost for a completion, or None when pricing is unknown."""
        del latency_ms
        pricing = self.models.get(usage.model)
        if pricing is None:
            return None

        billable_input = max(
            usage.input_tokens - usage.cached_input_tokens - usage.cache_creation_input_tokens,
            0,
        )
        cost = (
            Decimal(billable_input) * pricing.input_per_million
            + Decimal(usage.output_tokens) * pricing.output_per_million
        ) / Decimal(1_000_000)

        if usage.cached_input_tokens:
            cached_rate = pricing.cached_input_per_million or pricing.input_per_million
            cost += Decimal(usage.cached_input_tokens) * cached_rate / Decimal(1_000_000)

        if usage.cache_creation_input_tokens:
            creation_rate = pricing.cache_creation_input_per_million or pricing.input_per_million
            cost += Decimal(usage.cache_creation_input_tokens) * creation_rate / Decimal(1_000_000)

        return cost.quantize(Decimal('0.000001'))
