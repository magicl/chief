# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Anthropic Messages API provider."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, ClassVar

from anthropic import Anthropic
from anthropic.types import Usage
from anthropic.types.input_json_delta import InputJSONDelta
from anthropic.types.message_delta_usage import MessageDeltaUsage
from anthropic.types.text_delta import TextDelta
from anthropic.types.tool_use_block import ToolUseBlock
from libs.providers.base import (
    Delta,
    LLMProvider,
    ModelPricing,
    ProviderError,
    StreamResult,
)
from libs.providers.base import Usage as ChiefUsage
from libs.providers.errors import (
    MissingAnthropicCredentials,
    ProviderConfigurationError,
)
from libs.providers.spec import AnthropicProviderConfig
from libs.providers.types import ProviderLLMConfig
from libs.tools.base import qualified_tool_name_from_wire, wire_tool_name
from libs.tools.schema import ToolDefinition
from pydantic import BaseModel


class AnthropicProvider(LLMProvider):
    # Model IDs and USD/MTok — https://platform.claude.com/docs/en/about-claude/pricing
    models: ClassVar[dict[str, ModelPricing]] = {
        'claude-opus-4-8': ModelPricing(
            input_per_million=Decimal('5.00'),
            output_per_million=Decimal('25.00'),
            cached_input_per_million=Decimal('0.50'),
            cache_creation_input_per_million=Decimal('6.25'),
        ),
        'claude-sonnet-4-6': ModelPricing(
            input_per_million=Decimal('3.00'),
            output_per_million=Decimal('15.00'),
            cached_input_per_million=Decimal('0.30'),
            cache_creation_input_per_million=Decimal('3.75'),
        ),
        'claude-haiku-4-5': ModelPricing(
            input_per_million=Decimal('1.00'),
            output_per_million=Decimal('5.00'),
            cached_input_per_million=Decimal('0.10'),
            cache_creation_input_per_million=Decimal('1.25'),
        ),
        'claude-haiku-4.5': ModelPricing(
            input_per_million=Decimal('1.00'),
            output_per_million=Decimal('5.00'),
            cached_input_per_million=Decimal('0.10'),
            cache_creation_input_per_million=Decimal('1.25'),
        ),
    }

    def __init__(
        self,
        model: str,
        *,
        temperature: float | None = None,
        secret_supplier: Callable[[], str | None] | None = None,
    ) -> None:
        self.model = model.replace('.', '-')
        self.temperature = temperature
        self._secret_supplier = secret_supplier
        self._last_usage: ChiefUsage | None = None

    @classmethod
    def _from_spec(cls, provider_config: BaseModel, llm: ProviderLLMConfig) -> AnthropicProvider:
        _ = AnthropicProviderConfig.model_validate(provider_config.model_dump())
        return cls(llm.model, temperature=llm.temperature, secret_supplier=llm.secret_supplier)

    def _resolve_api_key(self) -> str | None:
        if self._secret_supplier is not None:
            return self._secret_supplier()
        return os.environ.get('ANTHROPIC_API_KEY')

    def get_client(self) -> Anthropic:
        api_key = self._resolve_api_key()
        if not api_key:
            raise MissingAnthropicCredentials()
        return Anthropic(api_key=api_key)

    def format_tools(self, definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                'name': wire_tool_name(definition.name),
                'description': definition.description,
                'input_schema': definition.parameters or {'type': 'object', 'properties': {}},
            }
            for definition in definitions
        ]

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> Iterator[Delta]:
        system, anthropic_messages = self._prepare_messages(messages)
        kwargs: dict[str, Any] = {
            'model': self.model,
            'max_tokens': 4096,
            'messages': anthropic_messages,
        }
        if system:
            kwargs['system'] = system
        if self.temperature is not None:
            kwargs['temperature'] = self.temperature
        tools = self.format_tools(tool_definitions)
        if tools:
            kwargs['tools'] = tools

        tool_index = 0
        with self.get_client().messages.stream(**kwargs) as stream:
            # Wire-format SSE events (RawMessageStreamEvent), not parsed TextEvent wrappers.
            for event in stream._raw_stream:  # pylint: disable=protected-access
                if event.type == 'message_start':
                    self._last_usage = self._usage_from_anthropic(event.message.usage)
                elif event.type == 'content_block_start':
                    block = event.content_block
                    if isinstance(block, ToolUseBlock):
                        yield Delta(
                            kind='tool_call',
                            tool_call_index=tool_index,
                            tool_call_id=block.id,
                            tool_name=block.name,
                            tool_arguments_delta='',
                        )
                        tool_index += 1
                elif event.type == 'content_block_delta':
                    delta = event.delta
                    if isinstance(delta, TextDelta):
                        yield Delta(kind='text', text=delta.text)
                    elif isinstance(delta, InputJSONDelta):
                        yield Delta(
                            kind='tool_call',
                            tool_call_index=max(tool_index - 1, 0),
                            tool_arguments_delta=delta.partial_json,
                        )
                elif event.type == 'message_delta':
                    prev = self._last_usage or ChiefUsage(model=self.model)
                    merged = self._usage_from_anthropic(event.usage)
                    self._last_usage = ChiefUsage(
                        model=self.model,
                        input_tokens=prev.input_tokens or merged.input_tokens,
                        output_tokens=merged.output_tokens or prev.output_tokens,
                        cached_input_tokens=merged.cached_input_tokens or prev.cached_input_tokens,
                        cache_creation_input_tokens=merged.cache_creation_input_tokens
                        or prev.cache_creation_input_tokens,
                    )

    def collect(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> StreamResult:
        started = time.monotonic()
        try:
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            usage: ChiefUsage | None = None
            self._last_usage = None

            for delta in self.stream(messages, tool_definitions):
                if delta.kind == 'text' and delta.text:
                    content_parts.append(delta.text)
                elif delta.kind == 'tool_call':
                    idx = delta.tool_call_index or 0
                    entry = tool_calls.setdefault(
                        idx,
                        {'id': '', 'name': '', 'arguments': ''},
                    )
                    if delta.tool_call_id:
                        entry['id'] = delta.tool_call_id
                    if delta.tool_name:
                        entry['name'] = qualified_tool_name_from_wire(delta.tool_name)
                    if delta.tool_arguments_delta:
                        entry['arguments'] += delta.tool_arguments_delta

            if self._last_usage:
                usage = self._last_usage

            assembled_calls = []
            for idx in sorted(tool_calls):
                call = tool_calls[idx]
                args_raw = call['arguments']
                try:
                    args = json.loads(args_raw) if args_raw else {}
                except json.JSONDecodeError:
                    args = {}
                assembled_calls.append(
                    {
                        'id': call['id'],
                        'name': call['name'],
                        'arguments': args,
                    }
                )

            return StreamResult(
                content=''.join(content_parts),
                tool_calls=assembled_calls,
                usage=usage,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except ProviderConfigurationError as exc:
            return StreamResult(
                error=ProviderError(message=exc.message, code=exc.code),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # pylint: disable=broad-except
            return StreamResult(
                error=ProviderError(message=str(exc), code='provider_failure'),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    def _usage_from_anthropic(self, raw_usage: Usage | MessageDeltaUsage) -> ChiefUsage:
        return ChiefUsage(
            model=self.model,
            input_tokens=raw_usage.input_tokens or 0,
            output_tokens=raw_usage.output_tokens or 0,
            cached_input_tokens=raw_usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=raw_usage.cache_creation_input_tokens or 0,
        )

    @staticmethod
    def _prepare_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg['role']
            if role == 'system':
                system_parts.append(msg.get('content', ''))
                continue

            if role == 'user':
                anthropic_messages.append({'role': 'user', 'content': msg.get('content', '')})
                continue

            if role == 'assistant':
                content: list[dict[str, Any]] = []
                text = msg.get('content', '')
                if text:
                    content.append({'type': 'text', 'text': text})
                for tc in msg.get('tool_calls', []):
                    fn = tc.get('function', {})
                    args = fn.get('arguments', {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    wire_name = fn['name']
                    content.append(
                        {
                            'type': 'tool_use',
                            'id': tc['id'],
                            'name': wire_tool_name(wire_name),
                            'input': args,
                        }
                    )
                anthropic_messages.append({'role': 'assistant', 'content': content or ''})
                continue

            if role == 'tool':
                anthropic_messages.append(
                    {
                        'role': 'user',
                        'content': [
                            {
                                'type': 'tool_result',
                                'tool_use_id': msg['tool_call_id'],
                                'content': msg.get('content', ''),
                            }
                        ],
                    }
                )

        return '\n\n'.join(system_parts), anthropic_messages
