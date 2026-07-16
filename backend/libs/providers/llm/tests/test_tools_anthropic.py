# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from anthropic.types import RawMessageStreamEvent
from anthropic.types.raw_content_block_start_event import RawContentBlockStartEvent
from anthropic.types.text_block import TextBlock
from apps.runner.tool_definitions import build_tool_definitions

# isort: split

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.providers.llm.anthropic_provider import AnthropicProvider
from libs.tools.context import ToolContext
from pydantic import TypeAdapter

from olib.py.django.test.cases import OTestCase

_STREAM_EVENT_ADAPTER: TypeAdapter[RawMessageStreamEvent] = TypeAdapter(RawMessageStreamEvent)


class TestToolsAnthropicProvider(OTestCase):
    def test_format_tools_uses_wire_safe_names(self) -> None:
        provider = AnthropicProvider('claude-haiku-4-5')
        ctx = ToolContext(spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'))
        definitions = build_tool_definitions(
            [ToolInstance(id='clock', type='clock', allow=['now'])],
            ctx=ctx,
            is_allowed=lambda *_args, **_kwargs: True,
        )
        tools = provider.format_tools(definitions)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]['name'], 'clock__now')
        self.assertNotIn('.', tools[0]['name'])

    def test_model_id_normalizes_dots_to_dashes(self) -> None:
        provider = AnthropicProvider('claude-haiku-4.5')
        self.assertEqual(provider.model, 'claude-haiku-4-5')

    def test_stream_event_adapter_typed_content_block(self) -> None:
        parsed = _STREAM_EVENT_ADAPTER.validate_python(
            {
                'type': 'content_block_start',
                'index': 0,
                'content_block': {'type': 'text', 'text': 'Hello'},
            }
        )
        self.assertIsInstance(parsed, RawContentBlockStartEvent)
        assert isinstance(parsed, RawContentBlockStartEvent)
        event = parsed
        self.assertEqual(event.index, 0)
        self.assertIsInstance(event.content_block, TextBlock)
        assert isinstance(event.content_block, TextBlock)
        self.assertEqual(event.content_block.text, 'Hello')
