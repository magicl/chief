# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.spec import LLMSpec
from apps.runner.providers.registry import make_provider

from olib.py.django.test.cases import OTestCase


class TestRepeatProvider(OTestCase):
    def test_collect_echoes_last_user_message(self) -> None:
        provider = make_provider(LLMSpec(provider='repeat', model='repeat'))
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
        provider = make_provider(LLMSpec(provider='repeat', model='repeat'))
        deltas = list(
            provider.stream(
                [{'role': 'user', 'content': 'ping'}],
                [],
            )
        )
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].text, 'ping')
