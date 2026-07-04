# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent config YAML validation."""

from apps.agents.services.config_validation import (
    ConfigValidationError,
    validate_agent_config_yaml,
)
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class ValidateAgentConfigYamlTests(OTestCase):
    def test_valid_example_yaml(self) -> None:
        spec = load_example('clock-assistant')
        raw = dump_agent_config_spec(spec)
        parsed = validate_agent_config_yaml(raw)
        self.assertEqual(parsed.tools[0].id, 'clock')

    def test_invalid_yaml_syntax(self) -> None:
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_agent_config_yaml('not: [valid')
        self.assertTrue(ctx.exception.errors)
        self.assertIsNotNone(ctx.exception.errors[0].line)

    def test_unknown_adapter_returns_structured_error(self) -> None:
        spec = load_example('queue-echo')
        spec = spec.model_copy(
            update={
                'queues': [
                    spec.queues[0].model_copy(
                        update={
                            'sources': [
                                spec.queues[0]
                                .sources[0]
                                .model_copy(
                                    update={'adapter_type': 'nonexistent-adapter'},
                                ),
                            ],
                        },
                    ),
                ],
            },
        )
        raw = dump_agent_config_spec(spec)
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_agent_config_yaml(raw)
        self.assertTrue(any('adapter' in e.message.lower() for e in ctx.exception.errors))

    def test_unknown_tool_returns_structured_error(self) -> None:
        spec = load_example('clock-assistant')
        spec = spec.model_copy(
            update={'tools': spec.tools + [type(spec.tools[0])(id='bad', type='missing', allow=['*'])]},
        )
        raw = dump_agent_config_spec(spec)
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_agent_config_yaml(raw)
        self.assertTrue(any('missing' in e.message for e in ctx.exception.errors))
