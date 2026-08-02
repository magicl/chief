# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import patch

import httpx
from anthropic import AuthenticationError
from libs.providers.llm.anthropic_provider import AnthropicProvider

from olib.py.django.test.cases import OTestCase


class TestAnthropicProviderFailures(OTestCase):
    def test_collect_captures_http_status_from_api_failure(self) -> None:
        """Preserve the HTTP status on ProviderError when the Anthropic SDK raises."""
        provider = AnthropicProvider('claude-sonnet-4-6')
        request = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
        response = httpx.Response(401, request=request, json={'error': {'message': 'invalid'}})
        api_failure = AuthenticationError(
            message='Error code: 401',
            response=response,
            body={'error': {'message': 'invalid'}},
        )
        with patch.object(provider, 'stream', side_effect=api_failure):
            result = provider.collect([{'role': 'user', 'content': 'hi'}], [])
        assert result.error is not None
        self.assertEqual(result.error.code, 'provider_failure')
        self.assertEqual(result.error.status_code, 401)

    def test_status_helpers_reject_bool_and_format_message(self) -> None:
        """Helpers keep bool out of status and format the curated message consistently."""
        from libs.providers.llm.base import (
            provider_request_failed_message,
            status_code_from_exception,
        )

        class _BoolStatus(Exception):
            status_code = True

        class _HttpStatus(Exception):
            status_code = 503

        self.assertIsNone(status_code_from_exception(_BoolStatus()))
        self.assertEqual(status_code_from_exception(_HttpStatus()), 503)
        self.assertEqual(provider_request_failed_message(), 'Provider request failed')
        self.assertEqual(provider_request_failed_message(status_code=401), 'Provider request failed (401)')
