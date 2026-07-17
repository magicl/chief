# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from libs.providers.llm.registry import make_provider
from libs.providers.llm.types import ProviderLLMConfig

from olib.py.django.test.cases import OTestCase


class TestRepeatProvider(OTestCase):
    def test_collect_echoes_last_user_message(self) -> None:
        provider = make_provider(ProviderLLMConfig(provider='repeat', model='repeat', user_id=0))
        result = provider.collect(
            [
                {'role': 'system', 'content': 'You are helpful.'},
                {'role': 'user', 'content': 'hello'},
                {'role': 'assistant', 'content': 'ignored'},
                {'role': 'user', 'content': 'repeat me'},
            ],
            [],
        )
        self.assertEqual(result.content, 'repeat me')

    def test_stream_yields_user_message(self) -> None:
        provider = make_provider(ProviderLLMConfig(provider='repeat', model='repeat', user_id=0))
        deltas = list(
            provider.stream(
                [{'role': 'user', 'content': 'ping'}],
                [],
            )
        )
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].text, 'ping')
