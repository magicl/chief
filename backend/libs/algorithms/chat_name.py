# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generate a short session title from the first user message."""

from __future__ import annotations

import logging
import re

from libs.providers.registry import make_provider
from libs.providers.types import ProviderLLMConfig
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    'You generate short chat titles. Reply with ONLY the title, no quotes or '
    'punctuation wrapper. Use the same language as the user message. '
    'Target 3–8 words.'
)


class ChatNameConfig(BaseModel):
    provider: str = 'openai'
    model: str = 'gpt-4o-mini'
    temperature: float = 0.2
    max_title_chars: int = 80
    enabled: bool = True


DEFAULT_CHAT_NAME_CONFIG = ChatNameConfig()


def generate_chat_name(
    first_message: str,
    *,
    config: ChatNameConfig | None = None,
    llm: ProviderLLMConfig | None = None,
) -> str:
    cfg = config or DEFAULT_CHAT_NAME_CONFIG
    message = first_message.strip()
    if not cfg.enabled:
        return _fallback_title(message, cfg)
    if not message:
        return _fallback_title(message, cfg)

    provider_config = llm or ProviderLLMConfig(
        provider=cfg.provider,
        model=cfg.model,
        temperature=cfg.temperature,
    )
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
        return _fallback_title(message, cfg)

    if result.error:
        logger.info('Chat name provider returned failure: %s', result.error.code)
        return _fallback_title(message, cfg)

    title = _sanitize_title(result.content, cfg.max_title_chars)
    if title:
        return title
    return _fallback_title(message, cfg)


def _sanitize_title(raw: str, max_len: int) -> str:
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
    text = ' '.join(message.split())
    if not text:
        return 'New chat'
    if len(text) > cfg.max_title_chars:
        return text[: cfg.max_title_chars - 1].rstrip() + '…'
    return text
