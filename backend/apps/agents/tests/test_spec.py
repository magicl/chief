# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from decimal import Decimal

from libs.agent_spec import (
    AGENT_CONFIG_SPEC_VERSION,
    AgentConfigSpec,
    LLMSpec,
    QueueSpec,
    SessionLimitsSpec,
    ToolInstance,
    TriggerSpec,
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
    'schema_version': 4,
    'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
    'system_prompt': 'hello',
    'triggers': [{'name': 'manual', 'kind': 'manual'}],
    'tools': [],
}


class TestAgentConfigSpec(OTestCase):
    def test_current_schema_version_constant(self) -> None:
        self.assertEqual(AGENT_CONFIG_SPEC_VERSION, 4)

    def test_tool_instance_requires_id_and_type(self) -> None:
        inst = ToolInstance(id='clock', type='clock', allow=['now'])
        self.assertEqual(inst.type, 'clock')

    def test_duplicate_instance_ids_rejected_at_spec_level(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec(
                schema_version=4,
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hi',
                tools=[
                    ToolInstance(id='a', type='clock', allow=['now']),
                    ToolInstance(id='a', type='clock', allow=['now']),
                ],
            )

    def test_load_spec_upgrades_v0_dict(self) -> None:
        spec = load_spec(V0_CLOCK_SPEC, stored_version=0)
        self.assertEqual(spec.schema_version, 4)
        self.assertEqual(spec.tools[0].id, 'clock')

    def test_integration_fills_tool_and_source(self) -> None:
        spec = load_spec(
            {
                'schema_version': 3,
                'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
                'system_prompt': 'hello',
                'integrations': [
                    {
                        'id': 'gmail-personal',
                        'type': 'gmail',
                        'credential_ref': 'gmail-personal',
                        'config': {'subject': 'me@example.com'},
                    }
                ],
                'tools': [
                    {
                        'id': 'gmail-personal',
                        'integration': 'gmail-personal',
                        'allow': ['list'],
                    }
                ],
                'queues': [
                    {
                        'id': 'inbox',
                        'sources': [
                            {
                                'id': 'gmail-main',
                                'integration': 'gmail-personal',
                                'config': {'query': 'in:inbox'},
                            }
                        ],
                    }
                ],
                'triggers': [{'name': 'manual', 'kind': 'manual'}],
            }
        )
        self.assertEqual(spec.tools[0].type, 'gmail')
        self.assertEqual(spec.tools[0].credential_ref, 'gmail-personal')
        self.assertEqual(spec.tools[0].config['subject'], 'me@example.com')
        source = spec.queues[0].sources[0]
        self.assertEqual(source.adapter_type, 'gmail')
        self.assertEqual(source.credential_ref, 'gmail-personal')
        self.assertEqual(source.config['subject'], 'me@example.com')
        self.assertEqual(source.config['query'], 'in:inbox')

    def test_unknown_integration_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            load_spec(
                {
                    **MINIMAL_SPEC_DICT,
                    'tools': [{'id': 'gmail', 'integration': 'missing', 'allow': ['list']}],
                }
            )

    def test_integration_type_conflict_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            load_spec(
                {
                    **MINIMAL_SPEC_DICT,
                    'integrations': [{'id': 'g', 'type': 'gmail', 'credential_ref': 'g'}],
                    'tools': [{'id': 'g', 'integration': 'g', 'type': 'clickup', 'allow': ['list']}],
                }
            )

    def test_explicit_null_credential_ref_opts_out_of_integration(self) -> None:
        spec = load_spec(
            {
                **MINIMAL_SPEC_DICT,
                'integrations': [
                    {
                        'id': 'gmail-personal',
                        'type': 'gmail',
                        'credential_ref': 'gmail-personal',
                        'config': {'subject': 'me@example.com'},
                    }
                ],
                'tools': [
                    {
                        'id': 'gmail-personal',
                        'integration': 'gmail-personal',
                        'credential_ref': None,
                        'allow': ['list'],
                    }
                ],
            }
        )
        self.assertIsNone(spec.tools[0].credential_ref)
        self.assertEqual(spec.tools[0].type, 'gmail')
        self.assertEqual(spec.tools[0].config['subject'], 'me@example.com')

    def test_valueless_yaml_keys_fall_back_to_defaults(self) -> None:
        """A bare ``integrations:`` key in YAML parses as None and must mean "omitted"."""
        spec = load_spec(
            {
                'schema_version': 4,
                'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
                'system_prompt': 'hello',
                'description': None,
                'limits': None,
                'integrations': None,
                'triggers': None,
                'tools': None,
                'queues': None,
                'skills': None,
            }
        )
        self.assertEqual(spec.integrations, [])
        self.assertEqual(spec.triggers, [])
        self.assertEqual(spec.tools, [])
        self.assertEqual(spec.queues, [])
        self.assertEqual(spec.skills, [])
        self.assertIsNone(spec.limits.max_iterations)
        self.assertIsNone(spec.description)

    def test_valueless_nested_keys_fall_back_to_defaults(self) -> None:
        spec = load_spec(
            {
                **MINIMAL_SPEC_DICT,
                'tools': [{'id': 'clock', 'type': 'clock', 'config': None, 'deny': None}],
                'queues': [{'id': 'inbox', 'sources': None}],
            }
        )
        self.assertEqual(spec.tools[0].config, {})
        self.assertEqual(spec.tools[0].allow, ['*'])
        self.assertEqual(spec.tools[0].deny, [])
        self.assertEqual(spec.queues[0].sources, [])
        self.assertEqual(spec.queues[0].max_attempts, 3)

    def test_valueless_allow_is_rejected_rather_than_allowing_everything(self) -> None:
        """A bare ``allow:`` must fail validation, not fall back to the permissive default."""
        with self.assertRaises(ValidationError):
            load_spec(
                {
                    **MINIMAL_SPEC_DICT,
                    'tools': [{'id': 'clock', 'type': 'clock', 'allow': None}],
                }
            )

    def test_valueless_aliased_source_key_falls_back_to_required(self) -> None:
        """``type:`` is an alias for adapter_type, so a bare key must read as missing."""
        with self.assertRaises(ValidationError) as caught:
            load_spec(
                {
                    **MINIMAL_SPEC_DICT,
                    'queues': [{'id': 'inbox', 'sources': [{'id': 'src-a', 'type': None}]}],
                }
            )
        self.assertIn('missing', str(caught.exception))

    def test_explicit_null_on_nullable_field_survives(self) -> None:
        """Dropping valueless keys must not disturb nulls that carry meaning."""
        spec = load_spec(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'sweep',
                        'kind': 'schedule',
                        'cron': '0 * * * *',
                        'prompt': 'Run sweep.',
                        'max_sessions': None,
                    },
                ],
            }
        )
        self.assertIsNone(spec.triggers[0].max_sessions)

    def test_duplicate_integration_ids_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            load_spec(
                {
                    **MINIMAL_SPEC_DICT,
                    'integrations': [
                        {'id': 'g', 'type': 'gmail'},
                        {'id': 'g', 'type': 'gmail'},
                    ],
                }
            )


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


class TestTriggerSpec(OTestCase):
    def test_queue_trigger_requires_queue_field(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'worker', 'kind': 'queue'}],
                    'queues': [{'id': 'inbox', 'sources': []}],
                }
            )

    def test_queue_trigger_must_reference_declared_queue(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'worker', 'kind': 'queue', 'queue': 'missing'}],
                    'queues': [{'id': 'inbox', 'sources': []}],
                }
            )

    def test_schedule_trigger_requires_cron(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'sweep',
                            'kind': 'schedule',
                            'prompt': 'Run sweep.',
                        },
                    ],
                }
            )

    def test_schedule_trigger_requires_prompt(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'sweep', 'kind': 'schedule', 'cron': '0 * * * *'}],
                }
            )

    def test_manual_trigger_rejects_prompt(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [{'name': 'manual', 'kind': 'manual', 'prompt': 'hello'}],
                }
            )

    def test_manual_max_sessions_defaults_to_none(self) -> None:
        spec = AgentConfigSpec.model_validate(MINIMAL_SPEC_DICT)
        self.assertIsNone(spec.triggers[0].max_sessions)

    def test_max_sessions_defaults_to_one_for_queue(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'worker',
                        'kind': 'queue',
                        'queue': 'inbox',
                        'prompt': 'Process items.',
                    },
                ],
                'queues': [{'id': 'inbox', 'sources': []}],
            }
        )
        self.assertEqual(spec.triggers[0].max_sessions, 1)

    def test_max_sessions_null_means_unlimited_for_schedule(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'sweep',
                        'kind': 'schedule',
                        'cron': '0 * * * *',
                        'prompt': 'Run sweep.',
                        'max_sessions': None,
                    },
                ],
            }
        )
        self.assertIsNone(spec.triggers[0].max_sessions)

    def test_max_sessions_omitted_defaults_to_one_for_schedule(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'sweep',
                        'kind': 'schedule',
                        'cron': '0 * * * *',
                        'prompt': 'Run sweep.',
                    },
                ],
            }
        )
        self.assertEqual(spec.triggers[0].max_sessions, 1)

    def test_button_trigger_requires_button_text(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'prompt': 'Triage now.',
                        },
                    ],
                }
            )

    def test_button_trigger_requires_prompt(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'Triage',
                        },
                    ],
                }
            )

    def test_button_trigger_rejects_cron(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'Triage',
                            'prompt': 'Triage now.',
                            'cron': '0 * * * *',
                        },
                    ],
                }
            )

    def test_button_trigger_rejects_empty_cron(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'Triage',
                            'prompt': 'Triage now.',
                            'cron': '',
                        },
                    ],
                }
            )

    def test_button_trigger_rejects_queue(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'Triage',
                            'prompt': 'Triage now.',
                            'queue': 'inbox',
                        },
                    ],
                    'queues': [{'id': 'inbox', 'sources': []}],
                }
            )

    def test_button_trigger_rejects_empty_queue(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'Triage',
                            'prompt': 'Triage now.',
                            'queue': '',
                        },
                    ],
                }
            )

    def test_button_trigger_rejects_overlong_button_text(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {
                            'name': 'triage',
                            'kind': 'button',
                            'button_text': 'x' * 41,
                            'prompt': 'Triage now.',
                        },
                    ],
                }
            )

    def test_trigger_blocks_default_to_empty_list(self) -> None:
        """Omitted blocks preserve the legacy unblocked trigger behavior."""
        spec = AgentConfigSpec.model_validate(MINIMAL_SPEC_DICT)
        self.assertEqual(spec.triggers[0].blocks, [])

    def test_trigger_accepts_explicit_empty_blocks(self) -> None:
        """An explicit empty block list is accepted as unblocked."""
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [{'name': 'manual', 'kind': 'manual', 'blocks': []}],
            }
        )
        self.assertEqual(spec.triggers[0].blocks, [])

    def test_tool_ready_block_references_declared_tool(self) -> None:
        """A tool_ready block round-trips when it names a declared tool."""
        raw = {
            **MINIMAL_SPEC_DICT,
            'tools': [{'id': 'vault', 'type': 'obsidian', 'config': {'vault': 'journal'}}],
            'triggers': [
                {
                    'name': 'run',
                    'kind': 'button',
                    'button_text': 'Run',
                    'prompt': 'Run now.',
                    'blocks': [{'kind': 'tool_ready', 'tool': 'vault'}],
                }
            ],
        }
        spec = AgentConfigSpec.model_validate(raw)
        self.assertEqual(
            spec.triggers[0].model_dump(mode='json')['blocks'],
            [{'kind': 'tool_ready', 'tool': 'vault'}],
        )

    def test_tool_ready_block_rejects_undeclared_tool(self) -> None:
        """Reject a tool_ready reference absent from the current spec."""
        with self.assertRaises(ValidationError) as caught:
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'tools': [{'id': 'clock', 'type': 'clock'}],
                    'triggers': [
                        {
                            'name': 'manual',
                            'kind': 'manual',
                            'blocks': [{'kind': 'tool_ready', 'tool': 'vault'}],
                        }
                    ],
                }
            )
        self.assertIn('vault', str(caught.exception))

    def test_tool_ready_block_requires_tool_id(self) -> None:
        """Require the tool id field on every tool_ready condition."""
        with self.assertRaises(ValidationError) as caught:
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {'name': 'manual', 'kind': 'manual', 'blocks': [{'kind': 'tool_ready'}]},
                    ],
                }
            )
        err = caught.exception.errors()[0]
        self.assertEqual(err['loc'], ('triggers', 0, 'blocks', 0, 'tool_ready', 'tool'))
        self.assertEqual(err['type'], 'missing')

    def test_unknown_block_kind_is_rejected(self) -> None:
        """Reject condition kinds without a supported schema and evaluator."""
        with self.assertRaises(ValidationError) as caught:
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'triggers': [
                        {'name': 'manual', 'kind': 'manual', 'blocks': [{'kind': 'future'}]},
                    ],
                }
            )
        err = caught.exception.errors()[0]
        self.assertEqual(err['loc'], ('triggers', 0, 'blocks', 0))
        self.assertEqual(err['type'], 'union_tag_invalid')
        self.assertIn("'future'", err['msg'])
        self.assertIn("'tool_ready'", err['msg'])

    def test_tool_ready_block_rejects_unknown_fields(self) -> None:
        """Forbid undeclared fields on the strict tool_ready condition."""
        with self.assertRaises(ValidationError) as caught:
            AgentConfigSpec.model_validate(
                {
                    **MINIMAL_SPEC_DICT,
                    'tools': [{'id': 'vault', 'type': 'obsidian'}],
                    'triggers': [
                        {
                            'name': 'manual',
                            'kind': 'manual',
                            'blocks': [{'kind': 'tool_ready', 'tool': 'vault', 'timeout': 30}],
                        }
                    ],
                }
            )
        err = caught.exception.errors()[0]
        self.assertEqual(err['loc'], ('triggers', 0, 'blocks', 0, 'tool_ready', 'timeout'))
        self.assertEqual(err['type'], 'extra_forbidden')

    def test_button_max_sessions_defaults_to_none(self) -> None:
        spec = AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'triage',
                        'kind': 'button',
                        'button_text': 'Triage',
                        'prompt': 'Triage now.',
                    },
                ],
            }
        )
        button = next(t for t in spec.triggers if t.kind == 'button')
        self.assertEqual(button.button_text, 'Triage')
        self.assertIsNone(button.max_sessions)


class TestSessionLimitsSpec(OTestCase):
    def test_session_limits_spec_defaults_to_uncapped(self) -> None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
        )
        self.assertIsNone(spec.limits.max_iterations)
        self.assertIsNone(spec.limits.max_cost_usd)

    def test_session_limits_spec_accepts_valid_values(self) -> None:
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            limits=SessionLimitsSpec(max_iterations=50, max_cost_usd=Decimal('2.00')),
        )
        self.assertEqual(spec.limits.max_iterations, 50)
        self.assertEqual(spec.limits.max_cost_usd, Decimal('2.00'))

    def test_session_limits_spec_accepts_dict_input(self) -> None:
        """Verify Pydantic coerces a raw dict (e.g. from YAML) into SessionLimitsSpec."""
        spec = AgentConfigSpec.model_validate(
            {
                'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
                'system_prompt': 'hello',
                'limits': {'max_iterations': 50, 'max_cost_usd': '2.00'},
            }
        )
        self.assertEqual(spec.limits.max_iterations, 50)
        self.assertEqual(spec.limits.max_cost_usd, Decimal('2.00'))

    def test_session_limits_rejects_negative_iterations(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfigSpec(
                llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
                system_prompt='hello',
                limits=SessionLimitsSpec(max_iterations=-1),
            )

    def test_trigger_spec_accepts_limit_fields(self) -> None:
        trigger = TriggerSpec(
            name='sweep',
            kind='schedule',
            cron='0 * * * *',
            prompt='go',
            max_iterations=20,
            max_cost_usd=Decimal('0.50'),
        )
        self.assertEqual(trigger.max_iterations, 20)
        self.assertEqual(trigger.max_cost_usd, Decimal('0.50'))


class TestToolInstanceConfig(OTestCase):
    def test_config_defaults_to_empty_dict(self) -> None:
        inst = ToolInstance(id='gmail-a', type='gmail')
        self.assertEqual(inst.config, {})

    def test_config_round_trips(self) -> None:
        inst = ToolInstance(id='gmail-a', type='gmail', config={'subject': 'me@example.com'})
        self.assertEqual(inst.config, {'subject': 'me@example.com'})
        self.assertEqual(inst.model_dump()['config'], {'subject': 'me@example.com'})
