# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent create/rename command helpers."""

from apps.agents.models import Agent
from apps.agents.services.config_commands import (
    ConfigCommandError,
    create_from_yaml,
    normalize_identifier,
    suggest_identifier,
)
from django.contrib.auth import get_user_model
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class AgentIdentityTests(OTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='identity-user', password='x')

    def test_normalize_identifier_from_display_name(self) -> None:
        self.assertEqual(normalize_identifier('Bob agent'), 'bob-agent')

    def test_suggest_identifier_adds_numeric_suffix(self) -> None:
        Agent.objects.create(user=self.user, name='Clock', identifier='clock')
        self.assertEqual(suggest_identifier(self.user.pk, 'clock'), 'clock-2')

    def test_create_from_yaml_requires_name(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('minimal'))
        with self.assertRaises(ConfigCommandError):
            create_from_yaml(self.user, spec_yaml, name='')

    def test_create_from_yaml_derives_identifier_from_name(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('minimal'))
        agent = create_from_yaml(self.user, spec_yaml, name='Bob agent')
        self.assertEqual(agent.name, 'Bob agent')
        self.assertEqual(agent.identifier, 'bob-agent')
