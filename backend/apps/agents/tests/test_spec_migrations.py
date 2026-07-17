# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.models import Agent, AgentConfig
from django.contrib.auth import get_user_model
from libs.agent_spec import (
    AGENT_CONFIG_SPEC_VERSION,
    detect_version,
    load_spec,
    load_spec_dict,
)
from libs.agent_spec.exceptions import (
    SpecMigrationError,
    UnsupportedSpecVersionError,
)
from libs.agent_spec.migrations import integrations as mig003
from libs.agent_spec.migrations import session_limits as mig004
from libs.agent_spec.migrations import tool_instances as mig001
from libs.agent_spec.migrations import trigger_prompts as mig002
from libs.agent_spec.registry import (
    get_spec_migrations,
    latest_spec_version,
)
from libs.agent_spec.trigger_prompts import (
    DEFAULT_QUEUE_TRIGGER_PROMPT,
    DEFAULT_SCHEDULE_TRIGGER_PROMPT,
)

from olib.py.django.test.cases import OTestCase

V0_CLOCK_SPEC = {
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [{'tool': 'clock', 'allow': ['now']}],
}

V1_SCHEDULE_SPEC = {
    'schema_version': 1,
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [
        {'name': 'manual', 'kind': 'manual'},
        {'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'},
        {'name': 'worker', 'kind': 'queue', 'queue': 'inbox'},
    ],
    'queues': [{'id': 'inbox', 'sources': []}],
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


class TestMigration002TriggerPrompts(OTestCase):
    def test_module_versions(self) -> None:
        self.assertEqual(mig002.FROM_VERSION, 1)
        self.assertEqual(mig002.TO_VERSION, 2)

    def test_upgrade_inserts_default_prompts_for_schedule_and_queue(self) -> None:
        out = mig002.upgrade(dict(V1_SCHEDULE_SPEC))

        self.assertEqual(out['schema_version'], 2)
        by_name = {t['name']: t for t in out['triggers']}
        self.assertNotIn('prompt', by_name['manual'])
        self.assertEqual(by_name['sweep']['prompt'], DEFAULT_SCHEDULE_TRIGGER_PROMPT)
        self.assertEqual(by_name['worker']['prompt'], DEFAULT_QUEUE_TRIGGER_PROMPT)

    def test_upgrade_preserves_existing_prompt(self) -> None:
        raw = dict(V1_SCHEDULE_SPEC)
        raw['triggers'] = [
            {'name': 'manual', 'kind': 'manual'},
            {'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *', 'prompt': 'Custom sweep.'},
        ]
        out = mig002.upgrade(raw)
        self.assertEqual(out['triggers'][1]['prompt'], 'Custom sweep.')


class TestMigration003Integrations(OTestCase):
    def test_module_versions(self) -> None:
        self.assertEqual(mig003.FROM_VERSION, 2)
        self.assertEqual(mig003.TO_VERSION, 3)

    def test_upgrade_adds_empty_integrations(self) -> None:
        raw = {
            'schema_version': 2,
            'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
            'system_prompt': 'hello',
            'tools': [],
        }
        out = mig003.upgrade(raw)
        self.assertEqual(out['schema_version'], 3)
        self.assertEqual(out['integrations'], [])


class TestMigration004SessionLimits(OTestCase):
    def test_module_versions(self) -> None:
        self.assertEqual(mig004.FROM_VERSION, 3)
        self.assertEqual(mig004.TO_VERSION, 4)

    def test_upgrade_adds_empty_limits(self) -> None:
        raw = {
            'schema_version': 3,
            'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
            'system_prompt': 'hello',
            'tools': [],
            'integrations': [],
        }
        out = mig004.upgrade(raw)
        self.assertEqual(out['schema_version'], 4)
        self.assertEqual(out['limits'], {})

    def test_upgrade_preserves_existing_limits(self) -> None:
        raw = {
            'schema_version': 3,
            'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
            'system_prompt': 'hello',
            'tools': [],
            'limits': {'max_iterations': 10},
        }
        out = mig004.upgrade(raw)
        self.assertEqual(out['schema_version'], 4)
        self.assertEqual(out['limits'], {'max_iterations': 10})


class TestSpecMigrationRegistry(OTestCase):
    def test_agent_config_spec_version_matches_registry(self) -> None:
        self.assertEqual(AGENT_CONFIG_SPEC_VERSION, latest_spec_version())

    def test_registry_has_contiguous_chain_starting_at_zero(self) -> None:
        steps = get_spec_migrations()
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0].from_version, 0)
        self.assertEqual(steps[0].to_version, 1)
        self.assertEqual(steps[1].from_version, 1)
        self.assertEqual(steps[1].to_version, 2)
        self.assertEqual(steps[2].from_version, 2)
        self.assertEqual(steps[2].to_version, 3)
        self.assertEqual(steps[3].from_version, 3)
        self.assertEqual(steps[3].to_version, 4)
        self.assertEqual(latest_spec_version(), 4)

    def test_detect_version_legacy_shape(self) -> None:
        self.assertEqual(detect_version(V0_CLOCK_SPEC), 0)

    def test_detect_version_from_schema_version_field(self) -> None:
        raw = {'schema_version': 1, 'tools': []}
        self.assertEqual(detect_version(raw), 1)

    def test_load_spec_dict_upgrades_v0(self) -> None:
        out = load_spec_dict(V0_CLOCK_SPEC, stored_version=0)
        self.assertEqual(out['schema_version'], 4)
        self.assertEqual(out['tools'][0]['id'], 'clock')
        self.assertEqual(out['integrations'], [])
        self.assertEqual(out['limits'], {})

    def test_load_spec_dict_upgrades_v1_schedule_spec(self) -> None:
        out = load_spec_dict(V1_SCHEDULE_SPEC, stored_version=1)
        self.assertEqual(out['schema_version'], 4)
        sweep = next(t for t in out['triggers'] if t['name'] == 'sweep')
        self.assertEqual(sweep['prompt'], DEFAULT_SCHEDULE_TRIGGER_PROMPT)

    def test_load_spec_dict_rejects_future_version(self) -> None:
        with self.assertRaises(UnsupportedSpecVersionError):
            load_spec_dict({'schema_version': 99, 'tools': []}, stored_version=99)

    def test_load_spec_validates_upgraded_v1_schedule(self) -> None:
        spec = load_spec(V1_SCHEDULE_SPEC, stored_version=1)
        self.assertEqual(spec.schema_version, 4)
        sweep = next(t for t in spec.triggers if t.name == 'sweep')
        self.assertEqual(sweep.prompt, DEFAULT_SCHEDULE_TRIGGER_PROMPT)


class TestAgentConfigGetSpec(OTestCase):
    def test_get_spec_upgrades_v0_row(self) -> None:
        user = get_user_model().objects.create_user(username='spec-v0', password='x')
        agent = Agent.objects.create(user=user, name='V0 agent', identifier='v0-agent')
        config = AgentConfig.objects.create(
            agent=agent,
            spec=dict(V0_CLOCK_SPEC),
            spec_version=0,
        )
        spec = config.get_spec()
        self.assertEqual(spec.schema_version, 4)
        self.assertEqual(spec.tools[0].id, 'clock')

    def test_get_spec_upgrades_v1_row_with_schedule_trigger(self) -> None:
        user = get_user_model().objects.create_user(username='spec-v1', password='x')
        agent = Agent.objects.create(user=user, name='V1 agent', identifier='v1-agent')
        config = AgentConfig.objects.create(
            agent=agent,
            spec=dict(V1_SCHEDULE_SPEC),
            spec_version=1,
        )
        spec = config.get_spec()
        sweep = next(t for t in spec.triggers if t.name == 'sweep')
        self.assertEqual(sweep.prompt, DEFAULT_SCHEDULE_TRIGGER_PROMPT)
