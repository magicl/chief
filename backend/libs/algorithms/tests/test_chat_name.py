# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from libs.algorithms.chat_name import (
    DEFAULT_CHAT_NAME_CONFIG,
    ChatNameConfig,
    ChatNameResult,
    generate_chat_name,
)
from libs.tools.activity import ActivityRef, NoOpActivityRecorder

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class _FakeRecorder:
    """Minimal in-memory ActivityRecorder capturing chat-name lifecycle calls.

    Only ``start``/``complete``/``fail`` are expected; the remaining protocol
    methods assert so an accidental span or sub-agent link fails the test.
    """

    def __init__(self) -> None:
        self.starts: list[str] = []
        self.completed = False
        self.failed = False
        self.completions: list[dict[str, Any]] = []

    def start(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'running',
    ) -> ActivityRef:
        """Record the started kind and return a synthetic handle."""
        del name, summary, details
        self.starts.append(kind)
        return ActivityRef(
            id=uuid4(),
            seq=len(self.starts),
            revision=1,
            kind=kind,
            status=status,
        )

    def complete(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'succeeded',
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Capture terminal metadata so tests can assert usage propagation."""
        del activity_id, details
        self.completed = True
        self.failed = self.failed or status == 'failed'
        self.completions.append(
            {
                'summary': summary,
                'status': status,
                'model': model,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cost_usd': cost_usd,
                'latency_ms': latency_ms,
            }
        )
        return ActivityRef(id=uuid4(), seq=1, revision=1, kind='llm', status=status)

    def fail(self, activity_id: UUID, **kwargs: Any) -> ActivityRef:
        """Route failures through ``complete`` with a failed status."""
        return self.complete(activity_id, status='failed', **kwargs)

    def status_note(self, **kwargs: Any) -> ActivityRef:
        """Reject status notes; chat naming must not emit them."""
        raise AssertionError('unexpected status_note')

    def span(self, **kwargs: Any) -> Any:
        """Reject spans; chat naming records a single llm activity."""
        raise AssertionError('unexpected span')

    def push_parent(self, activity_id: UUID | None) -> Any:
        """Reject parent overrides; the caller owns the parent scope."""
        raise AssertionError('unexpected push_parent')

    def link_subagent(self, **kwargs: Any) -> ActivityRef:
        """Reject sub-agent links; chat naming has no child session."""
        raise AssertionError('unexpected link_subagent')


class TestGenerateChatName(OTestCase):
    def test_default_config_uses_gpt_5_4_nano(self) -> None:
        """Session naming defaults to the cheap OpenAI nano model."""
        self.assertEqual(DEFAULT_CHAT_NAME_CONFIG.provider, 'openai')
        self.assertEqual(DEFAULT_CHAT_NAME_CONFIG.model, 'gpt-5.4-nano')

    def test_disabled_returns_fallback(self) -> None:
        result = generate_chat_name(
            'How do I reset my password?',
            config=ChatNameConfig(enabled=False),
        )
        self.assertEqual(result.title, 'How do I reset my password?')

    def test_repeat_provider_sanitizes_output(self) -> None:
        result = generate_chat_name(
            'Summarize quarterly revenue trends',
            config=ChatNameConfig(provider='repeat', model='repeat'),
        )
        self.assertTrue(result.title)
        self.assertLessEqual(len(result.title), 80)

    def test_empty_message_fallback(self) -> None:
        result = generate_chat_name('', config=ChatNameConfig(enabled=False))
        self.assertEqual(result.title, 'New chat')

    def test_long_message_truncated_in_fallback(self) -> None:
        long_message = 'word ' * 50
        result = generate_chat_name(long_message, config=ChatNameConfig(enabled=False))
        self.assertLessEqual(len(result.title), 80)
        self.assertTrue(result.title.endswith('…'))


class TestGenerateChatNameResult(OTestCase):
    def test_disabled_result_has_no_usage(self) -> None:
        """Fallback titles carry no provider usage for the caller to persist."""
        result = generate_chat_name('Hello there', config=ChatNameConfig(enabled=False))
        self.assertIsInstance(result, ChatNameResult)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.cost_usd)
        self.assertIsNone(result.model)
        self.assertFalse(result.provider_failed)

    def test_repeat_provider_result_carries_usage_and_model(self) -> None:
        result = generate_chat_name(
            'Summarize quarterly revenue',
            config=ChatNameConfig(provider='repeat', model='repeat'),
        )
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.model, 'repeat')
        self.assertFalse(result.provider_failed)


class TestGenerateChatNameRecorder(OTestCase):
    def test_disabled_does_not_open_a_session(self) -> None:
        recorder = NoOpActivityRecorder()
        result = generate_chat_name(
            'Hello there',
            config=ChatNameConfig(enabled=False),
            recorder=recorder,
        )
        self.assertEqual(result.title, 'Hello there')
        self.assertIsInstance(result, ChatNameResult)

    def test_disabled_records_no_activity(self) -> None:
        recorder = _FakeRecorder()
        generate_chat_name(
            'Hello there',
            config=ChatNameConfig(enabled=False),
            recorder=recorder,
        )
        self.assertEqual(recorder.starts, [])
        self.assertFalse(recorder.completed)

    def test_repeat_provider_records_llm_when_recorder_provided(self) -> None:
        recorder = _FakeRecorder()
        result = generate_chat_name(
            'Summarize quarterly revenue',
            config=ChatNameConfig(provider='repeat', model='repeat'),
            recorder=recorder,
        )
        self.assertTrue(result.title)
        self.assertIn('llm', recorder.starts)
        self.assertTrue(recorder.completed)
        self.assertFalse(recorder.failed)
        completion = recorder.completions[-1]
        self.assertEqual(completion['status'], 'succeeded')
        self.assertEqual(completion['model'], 'repeat')
        self.assertIsNotNone(completion['input_tokens'])

    @expectLogItems(
        [
            ExpectLogItem(
                'libs.algorithms.chat_name',
                logging.ERROR,
                r'Chat name provider call failed',
                count=1,
            )
        ]
    )
    def test_unknown_provider_fails_activity_and_falls_back(self) -> None:
        recorder = _FakeRecorder()
        result = generate_chat_name(
            'Summarize quarterly revenue',
            config=ChatNameConfig(provider='not_a_provider', model='nope'),
            recorder=recorder,
        )
        self.assertEqual(result.title, 'Summarize quarterly revenue')
        self.assertTrue(result.provider_failed)
        self.assertIn('llm', recorder.starts)
        self.assertTrue(recorder.failed)
