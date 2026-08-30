# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the trigger block-kind registry and the fail-closed dispatch gate."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

from apps.agents.block_gate import (
    UNEVALUATED_REASON,
    BlockGateResult,
    blocks_allow_dispatch,
    isolated_block_kinds,
    register_block_kind,
    registered_block_kinds,
)
from apps.agents.block_wiring import wire_block_kinds
from apps.agents.models import Agent, AgentConfig, Trigger, TriggerKind
from django.contrib.auth import get_user_model
from libs.agent_spec import AGENT_CONFIG_SPEC_VERSION
from libs.tools.readiness import BlockResult

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_LOGGER = 'apps.agents.block_gate'


class TestBlockGate(OTestCase):
    def setUp(self) -> None:
        """Create one agent plus config row that block-carrying triggers can hang off."""
        user = get_user_model().objects.create_user(username='block-gate', password='x')
        self.agent = Agent.objects.create(user_id=user.pk, name='Blocks', identifier='block-gate-agent')
        self.config = AgentConfig.objects.create(agent=self.agent, spec={}, source_rev='block-gate-v1')
        self._trigger_count = 0

    def _trigger_with_blocks(self, blocks: Any) -> Trigger:
        """Persist a queue trigger whose stored spec carries *blocks* verbatim."""
        self._trigger_count += 1
        name = f'worker-{self._trigger_count}'
        return Trigger.objects.create(
            agent=self.agent,
            agent_config=self.config,
            name=name,
            kind=TriggerKind.QUEUE,
            spec={'name': name, 'kind': 'queue', 'queue': 'inbox', 'blocks': blocks},
        )

    def _assert_unevaluated(self, result: BlockGateResult) -> None:
        """Malformed or unjudgeable conditions must block with the generic reason."""
        self.assertEqual(result, BlockGateResult(ready=False, reason=UNEVALUATED_REASON))

    def test_trigger_without_blocks_allows_dispatch(self) -> None:
        """A trigger spec with no blocks passes the gate without consulting the registry."""
        trigger = Trigger.objects.create(
            agent=self.agent,
            agent_config=self.config,
            name='no-blocks',
            kind=TriggerKind.QUEUE,
            spec={'name': 'no-blocks', 'kind': 'queue', 'queue': 'inbox'},
        )

        result = blocks_allow_dispatch(self.agent, trigger)

        self.assertEqual(result, BlockGateResult(ready=True))

    def test_empty_blocks_list_allows_dispatch(self) -> None:
        """An explicit empty list is the same as omitting blocks: no extra gates."""
        result = blocks_allow_dispatch(self.agent, self._trigger_with_blocks([]))

        self.assertEqual(result, BlockGateResult(ready=True))

    def test_non_mapping_spec_fails_closed(self) -> None:
        """A trigger whose spec is not a mapping cannot be judged and must not raise."""
        trigger = self._trigger_with_blocks([])
        trigger.spec = ['not', 'a', 'mapping']

        result = blocks_allow_dispatch(self.agent, trigger)

        self._assert_unevaluated(result)

    def test_present_blocks_none_fails_closed(self) -> None:
        """A present null blocks value is malformed, not an omitted list."""
        result = blocks_allow_dispatch(self.agent, self._trigger_with_blocks(None))

        self._assert_unevaluated(result)

    def test_truthy_non_list_blocks_fail_closed(self) -> None:
        """A present non-list blocks value must not be iterated or treated as ready."""
        for blocks in (1, {'kind': 'always_ready'}, 'tool_ready'):
            with self.subTest(blocks=blocks):
                result = blocks_allow_dispatch(self.agent, self._trigger_with_blocks(blocks))
                self._assert_unevaluated(result)

    def test_non_string_block_kind_fails_closed(self) -> None:
        """Kind values that cannot be looked up as slugs block without raising."""
        for kind in (['tool_ready'], {'k': 'v'}, 1):
            with self.subTest(kind=kind):
                result = blocks_allow_dispatch(self.agent, self._trigger_with_blocks([{'kind': kind}]))
                self._assert_unevaluated(result)

    def test_all_ready_blocks_allow_dispatch(self) -> None:
        """Every condition reporting ready leaves dispatch allowed."""
        with isolated_block_kinds():
            register_block_kind('always_ready', lambda _a, _t, _b: BlockResult(ready=True))
            trigger = self._trigger_with_blocks([{'kind': 'always_ready'}, {'kind': 'always_ready'}])

            result = blocks_allow_dispatch(self.agent, trigger)

            self.assertTrue(result.ready)
            self.assertEqual(result.reason, '')

    def test_blocks_short_circuit_in_declared_order(self) -> None:
        """Evaluation follows list order and stops at the first not-ready condition."""
        with isolated_block_kinds():
            calls: list[str] = []

            def evaluate(_agent: Agent, _trigger: Trigger, block: dict[str, Any]) -> BlockResult:
                """Record the evaluated entry and report it as not ready."""
                calls.append(block['name'])
                return BlockResult(ready=False, reason='dependency is pending')

            register_block_kind('test_ready', evaluate)
            trigger = self._trigger_with_blocks(
                [
                    {'kind': 'test_ready', 'name': 'first'},
                    {'kind': 'test_ready', 'name': 'second'},
                ]
            )

            result = blocks_allow_dispatch(self.agent, trigger)

            self.assertFalse(result.ready)
            self.assertEqual(result.reason, 'dependency is pending')
            self.assertEqual(calls, ['first'])

    def test_non_tool_kind_can_block_dispatch(self) -> None:
        """A kind registered outside the tool path blocks without any tool involvement."""
        with isolated_block_kinds():
            register_block_kind(
                'local_sync_idle',
                lambda _a, _t, _b: BlockResult(ready=False, reason='local sync is catching up'),
            )
            trigger = self._trigger_with_blocks([{'kind': 'local_sync_idle'}])

            result = blocks_allow_dispatch(self.agent, trigger)

            self.assertEqual(result, BlockGateResult(ready=False, reason='local sync is catching up'))

    def test_kind_without_handler_fails_closed(self) -> None:
        """An unhandled kind blocks dispatch instead of silently passing."""
        with isolated_block_kinds():
            trigger = self._trigger_with_blocks([{'kind': 'never_registered'}])

            result = blocks_allow_dispatch(self.agent, trigger)

            self._assert_unevaluated(result)

    def test_blocked_evaluation_logs_trigger_id_kind_and_safe_reason(self) -> None:
        """A blocked gate emits one info line with trigger id, kind, and the operator reason."""
        with isolated_block_kinds():
            register_block_kind(
                'test_ready',
                lambda _a, _t, _b: BlockResult(ready=False, reason='dependency is pending'),
            )
            trigger = self._trigger_with_blocks([{'kind': 'test_ready'}])

            with self.assertLogs(_LOGGER, level='INFO') as captured:
                result = blocks_allow_dispatch(self.agent, trigger)

            self.assertFalse(result.ready)
            self.assertEqual(len(captured.records), 1)
            message = captured.output[0]
            self.assertIn(str(trigger.pk), message)
            self.assertIn('test_ready', message)
            self.assertIn('dependency is pending', message)

    def test_allowed_evaluation_logs_trigger_id_and_kinds(self) -> None:
        """An allowed gate emits one info line with trigger id and the evaluated kinds."""
        with isolated_block_kinds():
            register_block_kind('always_ready', lambda _a, _t, _b: BlockResult(ready=True))
            trigger = self._trigger_with_blocks([{'kind': 'always_ready'}])

            with self.assertLogs(_LOGGER, level='INFO') as captured:
                result = blocks_allow_dispatch(self.agent, trigger)

            self.assertTrue(result.ready)
            self.assertEqual(len(captured.records), 1)
            message = captured.output[0]
            self.assertIn(str(trigger.pk), message)
            self.assertIn('always_ready', message)

    def test_missing_handler_emits_one_aggregate_info_log(self) -> None:
        """Missing handlers fail closed via the aggregate log, not a second missing-handler line."""
        with isolated_block_kinds():
            trigger = self._trigger_with_blocks([{'kind': 'never_registered'}])

            with self.assertLogs(_LOGGER, level='INFO') as captured:
                result = blocks_allow_dispatch(self.agent, trigger)

            self._assert_unevaluated(result)
            self.assertEqual(len(captured.records), 1)
            message = captured.output[0]
            self.assertIn(str(trigger.pk), message)
            self.assertIn(UNEVALUATED_REASON, message)
            self.assertNotIn('has no evaluator', message)

    @expectLogItems([ExpectLogItem(_LOGGER, logging.ERROR, r'block kind .* failed', count=1)])
    def test_raising_evaluator_fails_closed_without_propagating(self) -> None:
        """A raising handler blocks dispatch and never leaks its failure detail to callers."""
        with isolated_block_kinds():

            def evaluate(_agent: Agent, _trigger: Trigger, _block: dict[str, Any]) -> BlockResult:
                """Fail hard so the gate must absorb the failure."""
                raise RuntimeError('vault token abc123 rejected')

            register_block_kind('flaky_probe', evaluate)
            trigger = self._trigger_with_blocks([{'kind': 'flaky_probe'}])

            result = blocks_allow_dispatch(self.agent, trigger)

            self.assertFalse(result.ready)
            self.assertNotIn('abc123', result.reason)
            self.assertNotIn('RuntimeError', result.reason)

    def test_later_conditions_are_skipped_after_a_failing_handler(self) -> None:
        """Short-circuit also applies when the first condition has no handler."""
        with isolated_block_kinds():
            calls: list[str] = []

            def evaluate(_agent: Agent, _trigger: Trigger, _block: dict[str, Any]) -> BlockResult:
                """Record that the second condition was reached."""
                calls.append('second')
                return BlockResult(ready=True)

            register_block_kind('test_ready', evaluate)
            trigger = self._trigger_with_blocks([{'kind': 'never_registered'}, {'kind': 'test_ready'}])

            result = blocks_allow_dispatch(self.agent, trigger)

            self.assertFalse(result.ready)
            self.assertEqual(calls, [])

    def test_register_block_kind_rejects_invalid_slug(self) -> None:
        """Kind names must be lowercase slugs so YAML and registry keys stay aligned."""
        with isolated_block_kinds():
            for kind in ('Tool_Ready', 'tool-ready', '1tool', '', 'tool ready'):
                with self.assertRaises(ValueError):
                    register_block_kind(kind, lambda _a, _t, _b: BlockResult(ready=True))

    def test_isolated_block_kinds_restores_previous_registry(self) -> None:
        """Test isolation must not drop the startup-registered built-in kinds."""
        before = registered_block_kinds()

        with isolated_block_kinds():
            register_block_kind('temporary_kind', lambda _a, _t, _b: BlockResult(ready=True))
            self.assertEqual(registered_block_kinds(), frozenset({'temporary_kind'}))

        self.assertEqual(registered_block_kinds(), before)


class TestBlockKindWiring(OTestCase):
    def _agent_with_tools(self, tools: list[dict[str, Any]]) -> tuple[Agent, Trigger]:
        """Create an agent whose current spec and persisted trigger target one exact tool id."""
        user = get_user_model().objects.create_user(username=f'block-wiring-{Agent.objects.count()}', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Wiring', identifier=f'block-agent-{Agent.objects.count()}')
        config = AgentConfig.objects.create(
            agent=agent,
            spec={
                'schema_version': AGENT_CONFIG_SPEC_VERSION,
                'llm': {'provider': 'openai', 'model': 'gpt-5.4-mini'},
                'system_prompt': 'Test readiness.',
                'tools': tools,
            },
            spec_version=AGENT_CONFIG_SPEC_VERSION,
            source_rev='block-wiring-v1',
        )
        agent.current_config = config
        agent.save(update_fields=['current_config'])
        trigger = Trigger.objects.create(
            agent=agent,
            agent_config=config,
            name='worker',
            kind=TriggerKind.QUEUE,
            spec={'name': 'worker', 'kind': 'queue', 'blocks': [{'kind': 'tool_ready', 'tool': 'target'}]},
        )
        return agent, trigger

    def test_startup_registers_built_in_kinds(self) -> None:
        """AppConfig.ready() wiring leaves the built-in kinds available to callers."""
        self.assertIn('tool_ready', registered_block_kinds())

    def test_wire_block_kinds_is_idempotent(self) -> None:
        """Repeated wiring keeps exactly one handler per built-in kind."""
        with isolated_block_kinds():
            wire_block_kinds()
            first = registered_block_kinds()
            wire_block_kinds()

            self.assertEqual(registered_block_kinds(), first)
            self.assertIn('tool_ready', first)

    def test_tool_ready_uses_default_readiness_for_registered_clock(self) -> None:
        """A real registered clock instance is ready through the unpatched evaluator."""
        agent, trigger = self._agent_with_tools([{'id': 'target', 'type': 'clock'}])

        result = blocks_allow_dispatch(agent, trigger)

        self.assertEqual(result, BlockGateResult(ready=True))

    @patch('apps.agents.block_wiring.get_tool')
    def test_tool_ready_resolves_exact_current_spec_instance(self, get_tool: MagicMock) -> None:
        """Resolve the exact tools[].id, then invoke its registered type with runtime context."""
        agent, trigger = self._agent_with_tools(
            [
                {'id': 'other', 'type': 'clock'},
                {'id': 'target', 'type': 'clock'},
            ]
        )
        tool = MagicMock()
        tool.readiness.return_value = BlockResult(ready=True)
        get_tool.return_value = tool

        result = blocks_allow_dispatch(agent, trigger)

        self.assertTrue(result.ready)
        get_tool.assert_called_once_with('clock')
        ctx, instance = tool.readiness.call_args.args
        self.assertEqual(instance.id, 'target')
        self.assertEqual(ctx.user_id, agent.user_id)
        self.assertEqual(ctx.agent_id, agent.pk)
        assert agent.current_config is not None
        self.assertEqual(ctx.spec.tools, agent.current_config.get_spec().tools)
        self.assertEqual(ctx.client_factories, {})

    def test_tool_ready_without_current_spec_fails_closed(self) -> None:
        """An agent without a current config cannot prove a declared tool is ready."""
        user = get_user_model().objects.create_user(username='block-wiring', password='x')
        agent = Agent.objects.create(user_id=user.pk, name='Wiring', identifier='block-wiring-agent')
        config = AgentConfig.objects.create(agent=agent, spec={}, source_rev='block-wiring-v1')
        trigger = Trigger.objects.create(
            agent=agent,
            agent_config=config,
            name='worker',
            kind=TriggerKind.QUEUE,
            spec={'name': 'worker', 'kind': 'queue', 'blocks': [{'kind': 'tool_ready', 'tool': 'vault'}]},
        )

        result = blocks_allow_dispatch(agent, trigger)

        self.assertFalse(result.ready)
        self.assertEqual(result.reason, UNEVALUATED_REASON)

    def test_tool_ready_missing_instance_fails_closed(self) -> None:
        """A malformed persisted reference absent from the current tools list stays blocked."""
        agent, trigger = self._agent_with_tools([{'id': 'other', 'type': 'clock'}])

        result = blocks_allow_dispatch(agent, trigger)

        self.assertEqual(result, BlockGateResult(ready=False, reason=UNEVALUATED_REASON))

    @patch('apps.agents.block_wiring.get_tool', return_value=None)
    def test_tool_ready_missing_registered_type_fails_closed(self, _get_tool: MagicMock) -> None:
        """A spec tool whose implementation is not registered cannot open the gate."""
        agent, trigger = self._agent_with_tools([{'id': 'target', 'type': 'future_tool'}])

        result = blocks_allow_dispatch(agent, trigger)

        self.assertEqual(result, BlockGateResult(ready=False, reason=UNEVALUATED_REASON))

    @expectLogItems([ExpectLogItem(_LOGGER, logging.ERROR, r"block kind 'tool_ready' failed", count=1)])
    @patch('apps.agents.block_wiring.get_tool')
    def test_tool_ready_runtime_failure_fails_closed(self, get_tool: MagicMock) -> None:
        """A readiness implementation failure is absorbed by the outer fail-closed gate."""
        agent, trigger = self._agent_with_tools([{'id': 'target', 'type': 'clock'}])
        get_tool.return_value.readiness.side_effect = RuntimeError('secret abc123')

        result = blocks_allow_dispatch(agent, trigger)

        self.assertEqual(result, BlockGateResult(ready=False, reason=UNEVALUATED_REASON))
        self.assertNotIn('abc123', result.reason)
