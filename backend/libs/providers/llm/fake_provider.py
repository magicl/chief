# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Test double LLM provider with canned responses."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from libs.providers.llm.base import LLMProvider, StreamResult, Usage
from libs.providers.llm.types import ProviderLLMConfig
from libs.tools.schema import ToolDefinition
from pydantic import BaseModel


class FakeProvider(LLMProvider):
    def __init__(self, responses: list[StreamResult]) -> None:
        self._responses = list(responses)

    @classmethod
    def for_responses(cls, responses: list[StreamResult]) -> FakeProvider:
        return cls(responses)

    @classmethod
    def _from_spec(cls, provider_config: BaseModel, llm: ProviderLLMConfig) -> FakeProvider:
        del provider_config, llm
        return cls([])

    def format_tools(self, definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        del definitions
        return []

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> Iterator[Any]:
        del messages, tool_definitions
        return iter([])

    def collect(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> StreamResult:
        del messages, tool_definitions
        if not self._responses:
            return StreamResult(
                content='done',
                usage=Usage(model='fake', input_tokens=1, output_tokens=1),
                latency_ms=5,
            )
        return self._responses.pop(0)
