# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Expected session failures with user-visible messages."""

from __future__ import annotations


class SessionFailure(Exception):
    """Expected failure the UI should show without a worker traceback."""

    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class MissingOpenAICredentials(SessionFailure):
    def __init__(self) -> None:
        super().__init__('No OpenAI credentials specified', code='missing_openai_credentials')


class MissingAnthropicCredentials(SessionFailure):
    def __init__(self) -> None:
        super().__init__('No Anthropic credentials specified', code='missing_anthropic_credentials')


class UnsupportedLLMProvider(SessionFailure):
    def __init__(self, provider: str) -> None:
        super().__init__(f'Unsupported LLM provider: {provider}', code='unsupported_llm_provider')
