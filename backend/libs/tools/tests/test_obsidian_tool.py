# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Contract tests for the root-scoped Obsidian vault file tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

from django.test import override_settings
from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.obsidian.errors import (
    ObsidianAuthError,
    ObsidianConfigError,
    ObsidianForbiddenError,
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
    ObsidianUnavailableError,
)
from libs.clients.obsidian.protocol import ObsidianVaultClientProtocol
from libs.tools.context import ToolContext
from libs.tools.tools.clock import ClockTool
from libs.tools.tools.obsidian import (
    READINESS_UNAVAILABLE_REASON,
    ObsidianTool,
    _valid_arguments,
)

from olib.py.django.test.cases import OTestCase

_CONFIG = {'vault': 'Personal', 'roots': ['Journal']}


def _make_ctx(
    *,
    agent_id: Any = None,
    client_factory: Callable[..., ObsidianVaultClientProtocol] | None = None,
) -> ToolContext:
    """Build a minimal context with an optional agent id and client injection."""
    kwargs: dict[str, Any] = {
        'spec': AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
        'user_id': 1,
        'agent_id': agent_id,
    }
    if client_factory is not None:
        kwargs['client_factories'] = {'obsidian': client_factory}
    return ToolContext(**kwargs)


class TestObsidianTool(OTestCase):
    """Verify schemas, binding, dispatch, retry, and typed failure normalization."""

    def test_base_tool_readiness_defaults_ready(self) -> None:
        """Ordinary tools without an external startup dependency are ready by default."""
        result = ClockTool().readiness(
            _make_ctx(),
            ToolInstance(id='clock-instance', type='clock'),
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.reason, '')

    def test_readiness_reflects_injected_client_vault_status(self) -> None:
        """Probe the configured vault through the same injected factory used by file operations."""
        client = MagicMock()
        client.get_status.return_value = {'vault_id': 'Personal', 'ready': False}
        factory = MagicMock(return_value=client)
        agent_id = uuid4()
        instance = ToolInstance(id='vault', type='obsidian', config=_CONFIG)
        ctx = _make_ctx(
            agent_id=agent_id,
            client_factory=cast(Callable[..., ObsidianVaultClientProtocol], factory),
        )

        blocked = ObsidianTool().readiness(ctx, instance)
        client.get_status.return_value = {'vault_id': 'Personal', 'ready': True}
        ready = ObsidianTool().readiness(ctx, instance)

        self.assertFalse(blocked.ready)
        self.assertTrue(ready.ready)
        factory.assert_called_with(agent_id=str(agent_id), config=_CONFIG, instance_id='vault')
        client.get_status.assert_called_with(vault_id='Personal')

    def test_readiness_requires_literal_true_status(self) -> None:
        """Malformed or merely truthy status values cannot open the dispatch gate."""
        client = MagicMock()
        factory = cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client)
        tool = ObsidianTool()
        instance = ToolInstance(id='vault', type='obsidian', config=_CONFIG)

        for status in ({}, {'ready': None}, {'ready': 1}, {'ready': 'true'}):
            with self.subTest(status=status):
                client.get_status.return_value = status
                self.assertFalse(tool.readiness(_make_ctx(agent_id=uuid4(), client_factory=factory), instance).ready)

    def test_readiness_absorbs_client_failures_without_secret_details(self) -> None:
        """Provider and transport failures return a generic operator-safe not-ready result."""
        client = MagicMock()
        client.get_status.side_effect = ObsidianUnavailableError('token abc123 was rejected')
        instance = ToolInstance(id='vault', type='obsidian', config=_CONFIG)

        result = ObsidianTool().readiness(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            instance,
        )

        self.assertFalse(result.ready)
        self.assertNotIn('abc123', result.reason)
        self.assertNotIn('token', result.reason.lower())

    @override_settings(OBSIDIAN_VAULT_URL='', OBSIDIAN_VAULT_TOKEN='')
    def test_readiness_without_service_url_is_not_ready(self) -> None:
        """An installation without a configured vault service cannot confirm readiness."""
        result = ObsidianTool().readiness(
            _make_ctx(agent_id=uuid4()),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        self.assertFalse(result.ready)
        self.assertEqual(result.reason, READINESS_UNAVAILABLE_REASON)

    def test_exposes_exact_function_surface(self) -> None:
        """Expose list/read/write/append/status, marking reads and status read-only."""
        functions = {fn.name: fn for fn in ObsidianTool().functions(_make_ctx())}

        self.assertEqual(set(functions), {'list', 'read', 'write', 'append', 'status'})
        self.assertTrue(functions['list'].readonly)
        self.assertTrue(functions['read'].readonly)
        self.assertTrue(functions['status'].readonly)
        self.assertFalse(functions['write'].readonly)
        self.assertFalse(functions['append'].readonly)

    def test_declares_obsidian_credential_type(self) -> None:
        """Use the Obsidian credential type and namespace."""
        self.assertEqual(ObsidianTool.name, 'obsidian')
        self.assertEqual(ObsidianTool.credential_type, 'obsidian')

    def test_function_schemas_apply_exact_constraints(self) -> None:
        """Declare the exact closed schemas for file operations and status."""
        functions = {fn.name: fn for fn in ObsidianTool().functions(_make_ctx())}

        path = {
            'type': 'string',
            'minLength': 1,
            'maxLength': 4_096,
            'description': 'Vault-relative path (must resolve within one of the configured roots).',
        }
        content = {
            'type': 'string',
            'maxLength': 1_000_000,
            'description': 'UTF-8 markdown/text content.',
        }
        self.assertEqual(
            {name: function.parameters for name, function in functions.items()},
            {
                'list': {
                    'type': 'object',
                    'properties': {'path': path},
                    'required': ['path'],
                    'additionalProperties': False,
                },
                'read': {
                    'type': 'object',
                    'properties': {'path': path},
                    'required': ['path'],
                    'additionalProperties': False,
                },
                'write': {
                    'type': 'object',
                    'properties': {'path': path, 'content': content},
                    'required': ['path', 'content'],
                    'additionalProperties': False,
                },
                'append': {
                    'type': 'object',
                    'properties': {'path': path, 'content': content},
                    'required': ['path', 'content'],
                    'additionalProperties': False,
                },
                'status': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                    'additionalProperties': False,
                },
            },
        )

    def test_bind_passes_agent_id_config_and_instance_to_injected_factory(self) -> None:
        """Pass the string agent id, raw config, and instance identity to an injected factory."""
        agent_id = uuid4()
        client = MagicMock()
        client.list_dir.return_value = ['a.md']
        factory = MagicMock(return_value=client)
        instance = ToolInstance(id='vault', type='obsidian', config=_CONFIG)

        invoke = ObsidianTool().bind(
            _make_ctx(agent_id=agent_id, client_factory=cast(Callable[..., ObsidianVaultClientProtocol], factory)),
            instance,
        )
        self.assertEqual(invoke('list', {'path': 'Journal'}), {'ok': True, 'entries': ['a.md']})

        factory.assert_called_once_with(agent_id=str(agent_id), config=_CONFIG, instance_id='vault')

    def test_bind_default_factory_requires_agent_id(self) -> None:
        """Reject binding with a safe config failure when no agent id is available."""
        invoke = ObsidianTool().bind(
            _make_ctx(agent_id=None), ToolInstance(id='vault', type='obsidian', config=_CONFIG)
        )

        result = invoke('list', {'path': 'Journal'})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'config', 'message': 'obsidian tool requires an agent id'}},
        )

    @override_settings(OBSIDIAN_VAULT_URL='http://vault.internal', OBSIDIAN_VAULT_TOKEN='service-token')
    def test_bind_default_factory_builds_client_from_settings_and_agent_id(self) -> None:
        """Successfully bind a real ObsidianVaultClient from settings and the string agent id.

        Avoids exercising an actual network call: a malformed direct invocation reaches
        post-bind argument validation (a distinct failure message from any bind-time
        failure), which only happens once client construction has already succeeded.
        """
        invoke = ObsidianTool().bind(
            _make_ctx(agent_id=uuid4()),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('list', {})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'config', 'message': 'Obsidian tool arguments are invalid'}},
        )

    @override_settings(OBSIDIAN_VAULT_URL='', OBSIDIAN_VAULT_TOKEN='')
    def test_bind_default_factory_without_vault_settings_returns_config_failure(self) -> None:
        """Surface a safe config failure when the vault service is not configured."""
        invoke = ObsidianTool().bind(
            _make_ctx(agent_id=uuid4()),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('list', {'path': 'Journal'})

        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['kind'], 'config')

    def test_dispatches_exact_protocol_arguments(self) -> None:
        """Forward each function using the protocol's keyword-only contract with configured vault_id."""
        client = MagicMock()
        client.list_dir.return_value = ['a.md', 'sub']
        client.read_text.return_value = 'hello'
        client.get_status.return_value = {
            'vault_id': 'Personal',
            'ready': True,
            'initial_sync_complete': True,
            'sync_process_alive': True,
        }
        invoke = ObsidianTool().bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        self.assertEqual(invoke('list', {'path': 'Journal'}), {'ok': True, 'entries': ['a.md', 'sub']})
        self.assertEqual(invoke('read', {'path': 'Journal/a.md'}), {'ok': True, 'content': 'hello'})
        self.assertEqual(invoke('write', {'path': 'Journal/a.md', 'content': 'hi'}), {'ok': True})
        self.assertEqual(invoke('append', {'path': 'Journal/a.md', 'content': ' there'}), {'ok': True})
        self.assertEqual(
            invoke('status', {}),
            {
                'ok': True,
                'vault_id': 'Personal',
                'ready': True,
                'initial_sync_complete': True,
                'sync_process_alive': True,
            },
        )

        client.list_dir.assert_called_once_with(vault_id='Personal', path='Journal')
        client.read_text.assert_called_once_with(vault_id='Personal', path='Journal/a.md')
        client.write_text.assert_called_once_with(vault_id='Personal', path='Journal/a.md', content='hi')
        client.append_text.assert_called_once_with(vault_id='Personal', path='Journal/a.md', content=' there')
        client.get_status.assert_called_once_with(vault_id='Personal')

    def test_malformed_direct_invocations_return_safe_config_failures(self) -> None:
        """Normalize missing required arguments and unknown functions at the tool boundary."""
        client = MagicMock()
        invoke = ObsidianTool().bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        cases: tuple[tuple[str, dict[str, Any], str], ...] = (
            ('list', {}, 'Obsidian tool arguments are invalid'),
            ('write', {'path': 'a.md'}, 'Obsidian tool arguments are invalid'),
            ('append', {'content': 'x'}, 'Obsidian tool arguments are invalid'),
            ('status', {'path': 'Journal'}, 'Obsidian tool arguments are invalid'),
            ('delete', {}, 'Unknown Obsidian tool function'),
        )
        for function, arguments, expected_message in cases:
            with self.subTest(function=function):
                result = invoke(function, arguments)

                self.assertEqual(
                    result,
                    {'ok': False, 'error': {'kind': 'config', 'message': expected_message}},
                )
        client.assert_not_called()

    def test_adversarial_direct_invocations_never_reach_client(self) -> None:
        """Reject malformed direct payloads without raising or invoking the client."""
        client = MagicMock()
        invoke = ObsidianTool().bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )
        cases: tuple[tuple[Any, Any], ...] = (
            ('list', None),
            ('list', []),
            (None, {}),
            ('list', {'unexpected': True}),
            ('list', {'path': None}),
            ('list', {'path': ''}),
            ('list', {'path': 3}),
            ('write', {'path': 'a.md', 'content': None}),
            ('write', {'path': 'a.md', 'content': 3}),
            ('write', {'path': 'a.md', 'content': 'x', 'extra': 1}),
        )

        for function, arguments in cases:
            with self.subTest(function=function, arguments=arguments):
                result = invoke(function, arguments)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['kind'], 'config')
        self.assertEqual(client.mock_calls, [])

    def test_direct_validator_matches_published_constraints(self) -> None:
        """Match published schema semantics for boundary path/content values."""
        self.assertTrue(_valid_arguments('write', {'path': 'a.md', 'content': ''}))
        self.assertTrue(_valid_arguments('list', {'path': 'Journal'}))
        self.assertFalse(_valid_arguments('list', {'path': 'x' * 4_097}))
        self.assertFalse(_valid_arguments('write', {'path': 'a.md', 'content': 'x' * 1_000_001}))
        self.assertFalse(_valid_arguments('unknown', {'path': 'a.md'}))
        self.assertFalse(_valid_arguments('list', 'not-a-mapping'))
        self.assertTrue(_valid_arguments('status', {}))
        self.assertFalse(_valid_arguments('status', {'path': 'Journal'}))

    def test_maps_hard_typed_failures_to_common_kinds(self) -> None:
        """Normalize every non-retryable Obsidian client failure without exposing provider details."""
        cases = (
            (ObsidianAuthError('safe auth failure'), 'auth'),
            (ObsidianForbiddenError('safe forbidden failure'), 'forbidden'),
            (ObsidianOutsideRootError('safe root failure'), 'outside_root'),
            (ObsidianNotFoundError('safe missing failure'), 'not_found'),
            (ObsidianConfigError('safe config failure'), 'config'),
        )
        for failure, expected_kind in cases:
            with self.subTest(kind=expected_kind):
                client = MagicMock()
                client.list_dir.side_effect = failure
                factory = MagicMock(return_value=client)
                invoke = ObsidianTool().bind(
                    _make_ctx(
                        agent_id=uuid4(), client_factory=cast(Callable[..., ObsidianVaultClientProtocol], factory)
                    ),
                    ToolInstance(id='vault', type='obsidian', config=_CONFIG),
                )

                result = invoke('list', {'path': 'Journal'})

                self.assertEqual(
                    result,
                    {'ok': False, 'error': {'kind': expected_kind, 'message': str(failure)}},
                )

    def test_invalid_config_returns_config_failure_without_client(self) -> None:
        """Reject missing, empty, and malformed vault/roots before constructing a client."""
        invalid_configs: tuple[dict[str, Any], ...] = (
            {},
            {'vault': 'Personal', 'roots': []},
            {'vault': '', 'roots': ['Journal']},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                factory = MagicMock()
                invoke = ObsidianTool().bind(
                    _make_ctx(
                        agent_id=uuid4(),
                        client_factory=cast(Callable[..., ObsidianVaultClientProtocol], factory),
                    ),
                    ToolInstance(id='vault', type='obsidian', config=config),
                )

                result = invoke('list', {'path': 'Journal'})

                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['kind'], 'config')
                factory.assert_not_called()

    def test_retries_sync_pending_then_succeeds_with_injected_sleep(self) -> None:
        """Stall on sync_pending using the injected delay schedule, then return the eventual result."""
        client = MagicMock()
        client.read_text.side_effect = [
            ObsidianSyncPendingError('first sync not complete'),
            ObsidianSyncPendingError('first sync not complete'),
            'hello',
        ]
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1, 0.2)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('read', {'path': 'Journal/a.md'})

        self.assertEqual(result, {'ok': True, 'content': 'hello'})
        self.assertEqual(recorded_delays, [0.05, 0.1])
        self.assertEqual(client.read_text.call_count, 3)

    def test_retries_unavailable_then_succeeds_with_injected_sleep(self) -> None:
        """Retry a transient unavailable failure the same way as sync_pending."""
        client = MagicMock()
        client.append_text.side_effect = [ObsidianUnavailableError('vault service unreachable'), None]
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('append', {'path': 'Journal/a.md', 'content': 'x'})

        self.assertEqual(result, {'ok': True})
        self.assertEqual(recorded_delays, [0.05])
        self.assertEqual(client.append_text.call_count, 2)

    def test_retries_exhausted_returns_retryable_failure_kind(self) -> None:
        """Surface the retryable failure kind once the delay schedule is exhausted."""
        client = MagicMock()
        client.list_dir.side_effect = ObsidianSyncPendingError('first sync not complete')
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.0, 0.0)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('list', {'path': 'Journal'})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'sync_pending', 'message': 'first sync not complete'}},
        )
        self.assertEqual(recorded_delays, [0.0, 0.0])
        self.assertEqual(client.list_dir.call_count, 3)

    def test_status_does_not_retry_sync_pending(self) -> None:
        """Surface sync_pending from status immediately without sleeping."""
        client = MagicMock()
        client.get_status.side_effect = ObsidianSyncPendingError('first sync not complete')
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('status', {})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'sync_pending', 'message': 'first sync not complete'}},
        )
        self.assertEqual(recorded_delays, [])
        self.assertEqual(client.get_status.call_count, 1)

    def test_status_does_not_retry_unavailable(self) -> None:
        """Surface unavailable from status immediately without sleeping."""
        client = MagicMock()
        client.get_status.side_effect = ObsidianUnavailableError('vault service unreachable')
        recorded_delays: list[float] = []
        invoke = ObsidianTool(sleep=recorded_delays.append, delays=(0.05, 0.1)).bind(
            _make_ctx(
                agent_id=uuid4(),
                client_factory=cast(Callable[..., ObsidianVaultClientProtocol], lambda **_kwargs: client),
            ),
            ToolInstance(id='vault', type='obsidian', config=_CONFIG),
        )

        result = invoke('status', {})

        self.assertEqual(
            result,
            {'ok': False, 'error': {'kind': 'unavailable', 'message': 'vault service unreachable'}},
        )
        self.assertEqual(recorded_delays, [])
        self.assertEqual(client.get_status.call_count, 1)

    def test_default_construction_uses_real_sleep(self) -> None:
        """Default construction wires ``time.sleep`` so production callers stall for real."""
        import time

        self.assertIs(ObsidianTool()._sleep, time.sleep)  # pylint: disable=protected-access
