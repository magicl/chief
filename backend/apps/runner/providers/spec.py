# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Per-provider configuration bound at registry time (not in LLMSpec)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class OpenAIProviderConfig(BaseModel):
    """OpenAI uses environment credentials; no registry-level fields yet."""


class AnthropicProviderConfig(BaseModel):
    """Anthropic uses environment credentials; no registry-level fields yet."""


class LocalOpenAIProviderConfig(BaseModel):
    """Host and power-based cost parameters for a local OpenAI-compatible server."""

    hostname: str
    power_watts: Decimal = Field(default=Decimal('300'))
    power_usd_per_kwh: Decimal = Field(default=Decimal('0.15'))


class RepeatProviderConfig(BaseModel):
    """Repeat provider echoes user input; no registry-level fields."""
