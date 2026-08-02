# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from unittest.mock import patch

import httpx
from libs.providers.llm.openai_provider import OpenAIProvider
from openai import AuthenticationError

from olib.py.django.test.cases import OTestCase


class TestOpenAIProviderSampling(OTestCase):
    def test_collect_captures_http_status_from_api_failure(self) -> None:
        """Preserve the HTTP status on ProviderError when the OpenAI SDK raises."""
        provider = OpenAIProvider('gpt-5.4-mini')
        request = httpx.Request('POST', 'https://api.openai.com/v1/chat/completions')
        response = httpx.Response(401, request=request, json={'error': {'message': 'bad key'}})
        api_failure = AuthenticationError(
            message='Error code: 401',
            response=response,
            body={'error': {'message': 'bad key'}},
        )
        with patch.object(provider, 'stream', side_effect=api_failure):
            result = provider.collect([{'role': 'user', 'content': 'hi'}], [])
        assert result.error is not None
        self.assertEqual(result.error.code, 'provider_failure')
        self.assertEqual(result.error.status_code, 401)

    def test_gpt_5_5_omits_temperature(self) -> None:
        provider = OpenAIProvider('gpt-5.5', temperature=0.7)
        with patch.object(provider, 'get_client') as mock_get_client:
            mock_get_client.return_value.chat.completions.create.return_value = iter([])
            list(provider.stream([{'role': 'user', 'content': 'hi'}], []))
            kwargs = mock_get_client.return_value.chat.completions.create.call_args.kwargs
            self.assertNotIn('temperature', kwargs)

    def test_gpt_5_4_mini_sends_temperature(self) -> None:
        provider = OpenAIProvider('gpt-5.4-mini', temperature=0.7)
        with patch.object(provider, 'get_client') as mock_get_client:
            mock_get_client.return_value.chat.completions.create.return_value = iter([])
            list(provider.stream([{'role': 'user', 'content': 'hi'}], []))
            kwargs = mock_get_client.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs['temperature'], 0.7)

    def test_stream_uses_secret_supplier_over_env(self) -> None:
        provider = OpenAIProvider('gpt-5.4-mini', secret_supplier=lambda: 'sk-supplied')
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-env'}, clear=False):
            with patch.object(provider, 'get_client') as mock_get_client:
                mock_get_client.return_value.chat.completions.create.return_value = iter([])
                list(provider.stream([{'role': 'user', 'content': 'hi'}], []))
                mock_get_client.assert_called_once()

    def test_unknown_model_sends_temperature(self) -> None:
        provider = OpenAIProvider('custom-model', temperature=0.5)
        with patch.object(provider, 'get_client') as mock_get_client:
            mock_get_client.return_value.chat.completions.create.return_value = iter([])
            list(provider.stream([{'role': 'user', 'content': 'hi'}], []))
            kwargs = mock_get_client.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs['temperature'], 0.5)
