# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from libs.agent_spec import (
    AGENT_CONFIG_SPEC_VERSION,
    AgentConfigSpec,
    LLMSpec,
    QueueSpec,
    ToolInstance,
    load_spec,
)
from pydantic import ValidationError

from olib.py.django.test.cases import OTestCase

V0_CLOCK_SPEC = {
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [{'tool': 'clock', 'allow': ['now']}],
}

MINIMAL_SPEC_DICT = {
    'schema_version': 1,
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [],
}


class TestAgentConfigSpec(OTestCase):
    def test_current_schema_version_constant(self) -> None:
        self.assertEqual(AGENT_CONFIG_SPEC_VERSION, 1)

    def test_tool_instance_requires_id_and_type(self) -> None:
        inst = ToolInstance(id='clock', type='clock', allow=['now'])
        self.assertEqual(inst.type, 'clock')

    def test_duplicate_instance_ids_rejected_at_spec_level(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec(
                schema_version=1,
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hi',
                tools=[
                    ToolInstance(id='a', type='clock', allow=['now']),
                    ToolInstance(id='a', type='clock', allow=['now']),
                ],
            )

    def test_load_spec_upgrades_v0_dict(self) -> None:
        spec = load_spec(V0_CLOCK_SPEC, stored_version=0)
        self.assertEqual(spec.schema_version, 1)
        self.assertEqual(spec.tools[0].id, 'clock')


class TestQueueSpec(OTestCase):
    def test_queues_optional_defaults_empty(self) -> None:
        spec = AgentConfigSpec.model_validate(MINIMAL_SPEC_DICT)
        self.assertEqual(spec.queues, [])

    def test_queue_with_nested_sources(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'queues': [
                    {
                        'id': 'inbox',
                        'sources': [{'id': 'gmail-a', 'type': 'test', 'config': {'prefix': 'x'}}],
                    }
                ],
            }
        )
        self.assertEqual(spec.queues[0].id, 'inbox')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'test')

    def test_duplicate_queue_ids_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'queues': [
                        {'id': 'inbox', 'sources': []},
                        {'id': 'inbox', 'sources': []},
                    ],
                }
            )

    def test_duplicate_source_ids_in_queue_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'queues': [
                        {
                            'id': 'inbox',
                            'sources': [
                                {'id': 'src-a', 'type': 'test', 'config': {}},
                                {'id': 'src-a', 'type': 'test', 'config': {}},
                            ],
                        },
                    ],
                }
            )

    def test_queue_timing_fields_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            QueueSpec(id='inbox', max_attempts=0)

    def test_queue_hold_seconds_must_be_ordered(self) -> None:
        with self.assertRaises(ValidationError):
            QueueSpec(id='inbox', min_hold_seconds=300, early_release_seconds=60)
