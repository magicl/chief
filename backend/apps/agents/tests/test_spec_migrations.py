# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.models import Agent, AgentConfig
from apps.agents.spec import AGENT_CONFIG_SPEC_VERSION
from apps.agents.spec_migrations import detect_version, load_spec_dict
from apps.agents.spec_migrations.exceptions import (
    SpecMigrationError,
    UnsupportedSpecVersionError,
)
from apps.agents.spec_migrations.migrations import tool_instances as mig001
from apps.agents.spec_migrations.registry import (
    get_spec_migrations,
    latest_spec_version,
)
from django.contrib.auth import get_user_model

from olib.py.django.test.cases import OTestCase

V0_CLOCK_SPEC = {
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [{'tool': 'clock', 'allow': ['now']}],
}


class TestMigration001ToolInstances(OTestCase):
    def test_module_versions(self) -> None:
        self.assertEqual(mig001.FROM_VERSION, 0)
        self.assertEqual(mig001.TO_VERSION, 1)

    def test_upgrade_maps_tool_to_instance(self) -> None:
        out = mig001.upgrade(dict(V0_CLOCK_SPEC))
        self.assertEqual(out['schema_version'], 1)
        self.assertEqual(len(out['tools']), 1)
        inst = out['tools'][0]
        self.assertEqual(inst['id'], 'clock')
        self.assertEqual(inst['type'], 'clock')
        self.assertEqual(inst['allow'], ['now'])
        self.assertNotIn('tool', inst)

    def test_upgrade_rejects_duplicate_tool_names(self) -> None:
        raw = dict(V0_CLOCK_SPEC)
        raw['tools'] = [
            {'tool': 'clock', 'allow': ['now']},
            {'tool': 'clock', 'allow': ['now']},
        ]
        with self.assertRaises(SpecMigrationError):
            mig001.upgrade(raw)


class TestSpecMigrationRegistry(OTestCase):
    def test_agent_config_spec_version_matches_registry(self) -> None:
        self.assertEqual(AGENT_CONFIG_SPEC_VERSION, latest_spec_version())

    def test_registry_has_contiguous_chain_starting_at_zero(self) -> None:
        steps = get_spec_migrations()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].from_version, 0)
        self.assertEqual(steps[0].to_version, 1)
        self.assertEqual(latest_spec_version(), 1)

    def test_detect_version_legacy_shape(self) -> None:
        self.assertEqual(detect_version(V0_CLOCK_SPEC), 0)

    def test_detect_version_from_schema_version_field(self) -> None:
        raw = {'schema_version': 1, 'tools': []}
        self.assertEqual(detect_version(raw), 1)

    def test_load_spec_dict_upgrades_v0(self) -> None:
        out = load_spec_dict(V0_CLOCK_SPEC, stored_version=0)
        self.assertEqual(out['schema_version'], 1)
        self.assertEqual(out['tools'][0]['id'], 'clock')

    def test_load_spec_dict_rejects_future_version(self) -> None:
        with self.assertRaises(UnsupportedSpecVersionError):
            load_spec_dict({'schema_version': 99, 'tools': []}, stored_version=99)


class TestAgentConfigGetSpec(OTestCase):
    def test_get_spec_upgrades_v0_row(self) -> None:
        user = get_user_model().objects.create_user(username='spec-v0', password='x')
        agent = Agent.objects.create(user=user, identifier='v0-agent')
        config = AgentConfig.objects.create(
            agent=agent,
            spec=dict(V0_CLOCK_SPEC),
            spec_version=0,
        )
        spec = config.get_spec()
        self.assertEqual(spec.schema_version, 1)
        self.assertEqual(spec.tools[0].id, 'clock')
        self.assertEqual(spec.tools[0].type, 'clock')
