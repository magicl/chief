# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from libs.algorithms.chat_name import ChatNameConfig, generate_chat_name

from olib.py.django.test.cases import OTestCase


class TestGenerateChatName(OTestCase):
    def test_disabled_returns_fallback(self) -> None:
        title = generate_chat_name(
            'How do I reset my password?',
            config=ChatNameConfig(enabled=False),
        )
        self.assertEqual(title, 'How do I reset my password?')

    def test_repeat_provider_sanitizes_output(self) -> None:
        title = generate_chat_name(
            'Summarize quarterly revenue trends',
            config=ChatNameConfig(provider='repeat', model='repeat'),
        )
        self.assertTrue(title)
        self.assertLessEqual(len(title), 80)

    def test_empty_message_fallback(self) -> None:
        title = generate_chat_name('', config=ChatNameConfig(enabled=False))
        self.assertEqual(title, 'New chat')

    def test_long_message_truncated_in_fallback(self) -> None:
        long_message = 'word ' * 50
        title = generate_chat_name(long_message, config=ChatNameConfig(enabled=False))
        self.assertLessEqual(len(title), 80)
        self.assertTrue(title.endswith('…'))
