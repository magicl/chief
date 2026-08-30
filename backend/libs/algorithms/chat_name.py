# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generate a short session title from the first user message."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from libs.providers.llm.base import LLMProvider, StreamResult, Usage
from libs.providers.llm.registry import make_provider
from libs.providers.llm.types import ProviderLLMConfig
from libs.tools.activity import ActivityRecorder
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    'You generate short chat titles. Reply with ONLY the title, no quotes or '
    'punctuation wrapper. Use the same language as the user message. '
    'Target 3–8 words.'
)


class ChatNameConfig(BaseModel):
    provider: str = 'openai'
    model: str = 'gpt-5.4-nano'
    temperature: float = 0.2
    max_title_chars: int = 80
    enabled: bool = True


DEFAULT_CHAT_NAME_CONFIG = ChatNameConfig()


@dataclass
class ChatNameResult:
    """Title plus optional provider usage for the caller to persist.

    Usage fields stay None on fallback paths (naming disabled, empty message)
    because no provider call happened. ``provider_failed`` marks a title that
    came from the fallback after the provider was actually attempted.
    """

    title: str
    usage: Usage | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    model: str | None = None
    provider_failed: bool = False


def generate_chat_name(
    first_message: str,
    *,
    config: ChatNameConfig | None = None,
    llm: ProviderLLMConfig | None = None,
    recorder: ActivityRecorder | None = None,
) -> ChatNameResult:
    """Generate a short title; optionally record the llm call on ``recorder``.

    Does not create sessions. When ``recorder`` is omitted, behavior matches a
    plain provider call with no persistence. When provided, the ``llm``
    activity is created beneath the recorder's current parent (an in-tool span
    or an algorithm session root), so the caller decides where traces land.
    Fallback-only paths record nothing since no provider call is made.
    """
    cfg = config or DEFAULT_CHAT_NAME_CONFIG
    message = first_message.strip()
    if not cfg.enabled or not message:
        return ChatNameResult(title=_fallback_title(message, cfg))

    provider_config = llm or ProviderLLMConfig(
        provider=cfg.provider,
        model=cfg.model,
        temperature=cfg.temperature,
        user_id=0,
    )
    activity_id = _start_llm_activity(recorder, provider_config)
    try:
        provider = make_provider(provider_config)
        result = provider.collect(
            [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': f'User message:\n{message}'},
            ],
            [],
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception('Chat name provider call failed')
        _fail_llm_activity(
            recorder,
            activity_id,
            summary='Chat name provider call failed',
            model=provider_config.model,
        )
        return ChatNameResult(
            title=_fallback_title(message, cfg),
            model=provider_config.model,
            provider_failed=True,
        )

    cost_usd = _compute_cost(provider, result)
    model = result.usage.model if result.usage else provider_config.model

    if result.error:
        logger.info('Chat name provider returned failure: %s', result.error.code)
        _fail_llm_activity(
            recorder,
            activity_id,
            summary=f'Chat name provider failed: {result.error.code}',
            model=model,
            usage=result.usage,
            cost_usd=cost_usd,
            latency_ms=result.latency_ms,
        )
        return ChatNameResult(
            title=_fallback_title(message, cfg),
            usage=result.usage,
            cost_usd=cost_usd,
            latency_ms=result.latency_ms,
            model=model,
            provider_failed=True,
        )

    title = _sanitize_title(result.content, cfg.max_title_chars) or _fallback_title(message, cfg)
    if recorder is not None and activity_id is not None:
        recorder.complete(
            activity_id,
            summary=title,
            status='succeeded',
            model=model,
            input_tokens=result.usage.input_tokens if result.usage else None,
            output_tokens=result.usage.output_tokens if result.usage else None,
            cost_usd=cost_usd,
            latency_ms=result.latency_ms,
        )
    return ChatNameResult(
        title=title,
        usage=result.usage,
        cost_usd=cost_usd,
        latency_ms=result.latency_ms,
        model=model,
    )


def _start_llm_activity(
    recorder: ActivityRecorder | None,
    provider_config: ProviderLLMConfig,
) -> UUID | None:
    """Open the llm activity before the call so failures have something to fail."""
    if recorder is None:
        return None
    return recorder.start(
        kind='llm',
        name=provider_config.model,
        summary='Generating chat title',
        details={'provider': provider_config.provider},
    ).id


def _fail_llm_activity(
    recorder: ActivityRecorder | None,
    activity_id: UUID | None,
    *,
    summary: str,
    model: str | None,
    usage: Usage | None = None,
    cost_usd: Decimal | None = None,
    latency_ms: int | None = None,
) -> None:
    """Mark the llm activity failed, keeping any usage the provider reported."""
    if recorder is None or activity_id is None:
        return
    recorder.fail(
        activity_id,
        summary=summary,
        model=model,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def _compute_cost(provider: LLMProvider, result: StreamResult) -> Decimal | None:
    """Price the completion, tolerating providers without a pricing table."""
    if result.usage is None:
        return None
    return provider.compute_cost_usd(result.usage, latency_ms=result.latency_ms)


def _sanitize_title(raw: str, max_len: int) -> str:
    """Normalize provider output into a single-line title within ``max_len``."""
    text = ' '.join(raw.split())
    text = text.strip('"\'')
    text = re.sub(r'^title:\s*', '', text, flags=re.IGNORECASE)
    text = text.strip()
    if not text:
        return ''
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + '…'
    return text


def _fallback_title(message: str, cfg: ChatNameConfig) -> str:
    """Build a title from the user message when the provider is unused or failed."""
    text = ' '.join(message.split())
    if not text:
        return 'New chat'
    if len(text) > cfg.max_title_chars:
        return text[: cfg.max_title_chars - 1].rstrip() + '…'
    return text
