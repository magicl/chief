# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Test LLM provider that echoes the latest user message."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from libs.providers.types import ProviderLLMConfig
from libs.tools.schema import ToolDefinition
from libs.providers.base import Delta, LLMProvider, StreamResult, Usage
from pydantic import BaseModel


class RepeatProvider(LLMProvider):
    def __init__(self, model: str) -> None:
        self._model = model

    @classmethod
    def _from_spec(cls, provider_config: BaseModel, llm: ProviderLLMConfig) -> RepeatProvider:
        del provider_config
        return cls(llm.model)

    def format_tools(self, definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        del definitions
        return []

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> Iterator[Delta]:
        del tool_definitions
        content = _last_user_content(messages)
        if content:
            yield Delta(kind='text', text=content)

    def collect(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> StreamResult:
        del tool_definitions
        content = _last_user_content(messages)
        return StreamResult(
            content=content,
            usage=Usage(
                model=self._model,
                input_tokens=len(content),
                output_tokens=len(content),
            ),
            latency_ms=0,
        )


def _last_user_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get('role') != 'user':
            continue
        content = message.get('content', '')
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'text':
                    parts.append(str(part.get('text', '')))
            return ''.join(parts)
    return ''
