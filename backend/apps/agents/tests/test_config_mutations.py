# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for config editor mutations."""

from apps.agents.services.config_mutations import apply_config_mutation
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class ConfigMutationTests(OTestCase):
    def test_add_tool_instance(self) -> None:
        raw = dump_agent_config_spec(load_example('clock-assistant'))
        updated = apply_config_mutation(
            raw,
            {'action': 'add_tool', 'id': 'queue', 'type': 'queue', 'allow': ['take']},
        )
        self.assertIn('id: queue', updated)
        self.assertIn('type: queue', updated)
