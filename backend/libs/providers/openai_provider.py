# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""OpenAI chat completions provider."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, ClassVar

from libs.providers.base import (
    Delta,
    LLMProvider,
    ModelPricing,
    ProviderError,
    StreamResult,
    Usage,
)
from libs.providers.errors import MissingOpenAICredentials, ProviderConfigurationError
from libs.providers.spec import OpenAIProviderConfig
from libs.providers.types import ProviderLLMConfig
from libs.tools.base import qualified_tool_name_from_wire, wire_tool_name
from libs.tools.schema import ToolDefinition
from openai import OpenAI
from openai.types import CompletionUsage
from pydantic import BaseModel


class OpenAIProvider(LLMProvider):
    # Model IDs — https://developers.openai.com/api/docs/models/all
    # USD/MTok (standard tier) — https://developers.openai.com/api/docs/pricing
    models: ClassVar[dict[str, ModelPricing]] = {
        'gpt-5.5': ModelPricing(
            input_per_million=Decimal('5.00'),
            output_per_million=Decimal('30.00'),
            cached_input_per_million=Decimal('0.50'),
            supports_temperature=False,
        ),
        'gpt-5.4-mini': ModelPricing(
            input_per_million=Decimal('0.75'),
            output_per_million=Decimal('4.50'),
            cached_input_per_million=Decimal('0.075'),
        ),
        'gpt-5.4-nano': ModelPricing(
            input_per_million=Decimal('0.20'),
            output_per_million=Decimal('1.25'),
            cached_input_per_million=Decimal('0.02'),
        ),
    }

    def __init__(
        self,
        model: str,
        *,
        temperature: float | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        secret_supplier: Callable[[], str | None] | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._base_url = base_url
        self._api_key_override = api_key
        self._secret_supplier = secret_supplier
        self._last_usage: Usage | None = None

    @classmethod
    def _from_spec(cls, provider_config: BaseModel, llm: ProviderLLMConfig) -> OpenAIProvider:
        _ = OpenAIProviderConfig.model_validate(provider_config.model_dump())
        return cls(
            llm.model,
            temperature=llm.temperature,
            secret_supplier=llm.secret_supplier,
        )

    def _resolve_api_key(self) -> str | None:
        if self._api_key_override is not None:
            return self._api_key_override
        if self._secret_supplier is not None:
            return self._secret_supplier()
        return os.environ.get('OPENAI_API_KEY')

    def get_client(self) -> OpenAI:
        resolved_key = self._resolve_api_key()
        if not resolved_key:
            raise MissingOpenAICredentials()
        client_kwargs: dict[str, Any] = {'api_key': resolved_key}
        if self._base_url is not None:
            client_kwargs['base_url'] = self._base_url
        return OpenAI(**client_kwargs)

    def format_tools(self, definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                'type': 'function',
                'function': {
                    'name': wire_tool_name(definition.name),
                    'description': definition.description,
                    'parameters': definition.parameters,
                },
            }
            for definition in definitions
        ]

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_definitions: list[ToolDefinition],
    ) -> Iterator[Delta]:
        tools = self.format_tools(tool_definitions)
        kwargs: dict[str, Any] = {
            'model': self.model,
            'messages': self._prepare_messages(messages),
            'stream': True,
            'stream_options': {'include_usage': True},
        }
        if self.temperature is not None and self._supports_temperature():
            kwargs['temperature'] = self.temperature
        if tools:
            kwargs['tools'] = tools

        stream = self.get_client().chat.completions.create(**kwargs)
        for chunk in stream:
            if chunk.usage:
                self._last_usage = self._usage_from_openai(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield Delta(kind='text', text=delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    yield Delta(
                        kind='tool_call',
                        tool_call_index=tc.index,
                        tool_call_id=tc.id,
                        tool_name=tc.function.name if tc.function else None,
                        tool_arguments_delta=tc.function.arguments if tc.function else None,
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
            usage: Usage | None = None
            self._last_usage = None

            for delta in self.stream(messages, tool_definitions):
                if delta.kind == 'text' and delta.text:
                    content_parts.append(delta.text)
                elif delta.kind == 'tool_call':
                    idx = delta.tool_call_index or 0
                    entry = tool_calls.setdefault(
                        idx,
                        {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}},
                    )
                    if delta.tool_call_id:
                        entry['id'] = delta.tool_call_id
                    if delta.tool_name:
                        entry['function']['name'] = qualified_tool_name_from_wire(delta.tool_name)
                    if delta.tool_arguments_delta:
                        entry['function']['arguments'] += delta.tool_arguments_delta

            if self._last_usage:
                usage = self._last_usage

            assembled_calls = []
            for idx in sorted(tool_calls):
                call = tool_calls[idx]
                args_raw = call['function']['arguments']
                try:
                    args = json.loads(args_raw) if args_raw else {}
                except json.JSONDecodeError:
                    args = {}
                assembled_calls.append(
                    {
                        'id': call['id'],
                        'name': call['function']['name'],
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

    def _supports_temperature(self) -> bool:
        pricing = self.models.get(self.model)
        if pricing is None:
            return True
        return pricing.supports_temperature

    def _usage_from_openai(self, raw_usage: CompletionUsage) -> Usage:
        cached = 0
        if raw_usage.prompt_tokens_details is not None:
            cached = raw_usage.prompt_tokens_details.cached_tokens or 0
        return Usage(
            model=self.model,
            input_tokens=raw_usage.prompt_tokens or 0,
            output_tokens=raw_usage.completion_tokens or 0,
            cached_input_tokens=cached,
        )

    @staticmethod
    def _prepare_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for msg in messages:
            out = dict(msg)
            if 'tool_calls' in out:
                tool_calls = []
                for tc in out['tool_calls']:
                    fn = tc.get('function', {})
                    args = fn.get('arguments', {})
                    if not isinstance(args, str):
                        args = json.dumps(args)
                    tool_calls.append(
                        {
                            'id': tc['id'],
                            'type': 'function',
                            'function': {
                                'name': wire_tool_name(fn['name']),
                                'arguments': args,
                            },
                        }
                    )
                out['tool_calls'] = tool_calls
            prepared.append(out)
        return prepared
