# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Contract tests for the root-safe Dropbox metadata tool."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from unittest.mock import MagicMock

from apps.keys.exceptions import KeyNotFoundError, KeyTypeMismatchError
from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.dropbox.client import DropboxClient
from libs.clients.dropbox.errors import (
    DropboxAPIError,
    DropboxAuthError,
    DropboxConfigError,
    DropboxForbiddenError,
    DropboxInvalidCursorError,
    DropboxNotFoundError,
    DropboxOutsideRootError,
    DropboxRateLimitedError,
)
from libs.tools.context import ToolContext
from libs.tools.tools.dropbox import DropboxTool, _valid_arguments

from olib.py.django.test.cases import OTestCase

_CONFIG = {'namespace_id': 'team-space', 'roots': [{'id': 'shared', 'path': '/Shared'}]}


def _schema_accepts(schema: Mapping[str, Any], value: Any) -> bool:
    """Interpret the small JSON-schema subset published by these tool tests."""
    schema_type = schema.get('type')
    if schema_type == 'object':
        if not isinstance(value, Mapping):
            return False
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        return (
            all(name in value for name in required)
            and (schema.get('additionalProperties') is not False or set(value).issubset(properties))
            and all(name not in value or _schema_accepts(properties[name], item) for name, item in value.items())
        )
    if schema_type == 'string':
        return (
            isinstance(value, str)
            and schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', len(value))
            and ('enum' not in schema or value in schema['enum'])
        )
    if schema_type == 'integer':
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and schema.get('minimum', value) <= value <= schema.get('maximum', value)
        )
    if schema_type == 'array':
        return (
            isinstance(value, list)
            and len(value) <= schema.get('maxItems', len(value))
            and all(_schema_accepts(schema['items'], item) for item in value)
        )
    return False


class _SupplierResolvingClient:
    """Resolve the injected token only when an operation is invoked."""

    def __init__(self, *, token_supplier: Callable[[], str | None], **_kwargs: Any) -> None:
        """Retain the lazy supplier for a later metadata operation."""
        self._token_supplier = token_supplier

    def list_roots(self) -> Any:
        """Resolve credentials to model production client operation startup."""
        return self._token_supplier()


def _make_ctx(
    *,
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]] | None = None,
    client_factory: Callable[..., DropboxClient] | None = None,
) -> ToolContext:
    """Build a minimal context with optional credential and client injection."""
    kwargs: dict[str, Any] = {
        'spec': AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
        'user_id': 1,
    }
    if secret_supplier_factory is not None:
        kwargs['secret_supplier_factory'] = secret_supplier_factory
    if client_factory is not None:
        kwargs['client_factories'] = {'dropbox': client_factory}
    return ToolContext(**kwargs)


class TestDropboxTool(OTestCase):
    """Verify schemas, binding, dispatch, and typed failure normalization."""

    def test_exposes_exact_readonly_metadata_surface(self) -> None:
        """Expose only the four metadata operations and mark each read-only."""
        functions = {fn.name: fn for fn in DropboxTool().functions(_make_ctx())}

        self.assertEqual(set(functions), {'list_roots', 'list_folder', 'get_metadata', 'search'})
        self.assertTrue(all(fn.readonly for fn in functions.values()))
        self.assertTrue(all('metadata' in fn.description.lower() for fn in functions.values()))
        self.assertTrue(all('content' in fn.description.lower() for fn in functions.values()))
        self.assertTrue(all('web_url' in fn.description for fn in functions.values()))

    def test_declares_dropbox_credential_type(self) -> None:
        """Use the Dropbox credential type and namespace."""
        self.assertEqual(DropboxTool.name, 'dropbox')
        self.assertEqual(DropboxTool.credential_type, 'dropbox')

    def test_function_schemas_apply_exact_constraints(self) -> None:
        """Declare the exact closed schemas for all four metadata operations."""
        functions = {fn.name: fn for fn in DropboxTool().functions(_make_ctx())}

        root = {
            'type': 'string',
            'minLength': 1,
            'maxLength': 256,
            'description': 'Configured root alias that bounds all metadata access.',
        }
        cursor = {
            'type': 'string',
            'minLength': 1,
            'maxLength': 131_072,
            'description': 'Opaque cursor from a prior matching call.',
        }
        max_results = {
            'type': 'integer',
            'default': 50,
            'minimum': 1,
            'maximum': 100,
        }
        self.assertEqual(
            {name: function.parameters for name, function in functions.items()},
            {
                'list_roots': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                    'additionalProperties': False,
                },
                'list_folder': {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'folder_ref': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': 4_096,
                            'description': 'Folder item reference; defaults to the selected root.',
                        },
                        'cursor': cursor,
                        'max_results': max_results,
                    },
                    'required': ['root'],
                    'additionalProperties': False,
                },
                'get_metadata': {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'item_ref': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': 4_096,
                            'description': 'Dropbox item reference beneath the root.',
                        },
                    },
                    'required': ['root', 'item_ref'],
                    'additionalProperties': False,
                },
                'search': {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'query': {
                            'type': 'string',
                            'maxLength': 1_000,
                            'description': 'Native Dropbox metadata search text.',
                        },
                        'kinds': {
                            'type': 'array',
                            'maxItems': 2,
                            'items': {'type': 'string', 'enum': ['file', 'folder']},
                        },
                        'cursor': cursor,
                        'max_results': max_results,
                    },
                    'required': ['root', 'query'],
                    'additionalProperties': False,
                },
            },
        )

    def test_bind_passes_exact_factory_and_supplier_arguments(self) -> None:
        """Pass the original config, instance identity, and lazy Dropbox supplier."""
        resolved: list[tuple[str | None, str]] = []
        supplied = lambda: '{"refresh_token": true}'

        def resolve_supplier(ref: str | None, typ: str) -> Callable[[], str | None]:
            """Record credential resolution and return the stable supplier."""
            resolved.append((ref, typ))
            return supplied

        secret_factory = MagicMock(side_effect=resolve_supplier)
        client = MagicMock()
        client.list_roots.return_value = {'items': [], 'next_cursor': None}
        factory = MagicMock(return_value=client)
        instance = ToolInstance(
            id='files',
            type='dropbox',
            credential_ref='work-dropbox',
            config=_CONFIG,
        )

        invoke = DropboxTool().bind(
            _make_ctx(
                secret_supplier_factory=secret_factory,
                client_factory=cast(Callable[..., DropboxClient], factory),
            ),
            instance,
        )
        self.assertEqual(invoke('list_roots', {}), {'items': [], 'next_cursor': None})

        self.assertEqual(resolved, [('work-dropbox', 'dropbox')])
        factory.assert_called_once()
        factory_kwargs = factory.call_args.kwargs
        self.assertEqual(set(factory_kwargs), {'token_supplier', 'config', 'instance_id'})
        self.assertEqual(factory_kwargs['token_supplier'](), '{"refresh_token": true}')
        self.assertIs(factory_kwargs['config'], instance.config)
        self.assertEqual(factory_kwargs['instance_id'], 'files')

    def test_dispatches_exact_protocol_arguments(self) -> None:
        """Forward each function using the protocol's keyword-only contract."""
        client = MagicMock()
        client.list_roots.return_value = {'items': [], 'next_cursor': None}
        client.list_folder.return_value = {'items': [], 'next_cursor': None}
        client.get_metadata.return_value = {'item': {'id': 'item-1'}}
        client.search.return_value = {'items': [], 'next_cursor': None}
        invoke = DropboxTool().bind(
            _make_ctx(client_factory=cast(Callable[..., DropboxClient], lambda **_kwargs: client)),
            ToolInstance(id='files', type='dropbox', config=_CONFIG),
        )

        invoke('list_roots', {})
        invoke('list_folder', {'root': 'shared', 'folder_ref': 'folder-1', 'cursor': 'c1', 'max_results': 12})
        invoke('get_metadata', {'root': 'shared', 'item_ref': 'item-1'})
        invoke(
            'search',
            {'root': 'shared', 'query': 'budget', 'kinds': ['folder', 'file'], 'cursor': 'c2', 'max_results': 7},
        )

        client.list_roots.assert_called_once_with()
        client.list_folder.assert_called_once_with(
            root='shared',
            folder_ref='folder-1',
            cursor='c1',
            max_results=12,
        )
        client.get_metadata.assert_called_once_with(root='shared', item_ref='item-1')
        client.search.assert_called_once_with(
            root='shared',
            query='budget',
            kinds=('folder', 'file'),
            cursor='c2',
            max_results=7,
        )

    def test_dispatch_applies_protocol_defaults(self) -> None:
        """Supply stable defaults when optional arguments are omitted."""
        client = MagicMock()
        invoke = DropboxTool().bind(
            _make_ctx(client_factory=cast(Callable[..., DropboxClient], lambda **_kwargs: client)),
            ToolInstance(id='files', type='dropbox', config=_CONFIG),
        )

        invoke('list_folder', {'root': 'shared'})
        invoke('search', {'root': 'shared', 'query': 'budget'})

        client.list_folder.assert_called_once_with(
            root='shared',
            folder_ref=None,
            cursor=None,
            max_results=50,
        )
        client.search.assert_called_once_with(
            root='shared',
            query='budget',
            kinds=(),
            cursor=None,
            max_results=50,
        )

    def test_malformed_direct_invocations_return_safe_config_failures(self) -> None:
        """Normalize missing required arguments and unknown functions at the tool boundary."""
        client = MagicMock()
        invoke = DropboxTool().bind(
            _make_ctx(client_factory=cast(Callable[..., DropboxClient], lambda **_kwargs: client)),
            ToolInstance(id='files', type='dropbox', config=_CONFIG),
        )

        cases: tuple[tuple[str, dict[str, Any], str], ...] = (
            ('list_folder', {}, 'Dropbox tool arguments are invalid'),
            ('get_metadata', {'root': 'shared'}, 'Dropbox tool arguments are invalid'),
            ('search', {'root': 'shared'}, 'Dropbox tool arguments are invalid'),
            ('download', {}, 'Unknown Dropbox tool function'),
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
        invoke = DropboxTool().bind(
            _make_ctx(client_factory=cast(Callable[..., DropboxClient], lambda **_kwargs: client)),
            ToolInstance(id='files', type='dropbox', config=_CONFIG),
        )
        cases: tuple[tuple[Any, Any], ...] = (
            ('list_roots', None),
            ('list_roots', []),
            (None, {}),
            ('list_roots', {'unexpected': True}),
            ('list_folder', {'root': None}),
            ('list_folder', {'root': 'shared', 'folder_ref': 3}),
            ('list_folder', {'root': 'shared', 'cursor': 3}),
            ('list_folder', {'root': 'shared', 'max_results': True}),
            ('list_folder', {'root': 'shared', 'max_results': 0}),
            ('list_folder', {'root': 'shared', 'max_results': 101}),
            ('get_metadata', {'root': 'shared', 'item_ref': None}),
            ('search', {'root': 'shared', 'query': None}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': None}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [{}]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [[]]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [True]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': ['file', 1]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': ['file', 'folder', 'file']}),
        )

        for function, arguments in cases:
            with self.subTest(function=function, arguments=arguments):
                self.assertEqual(
                    invoke(function, arguments),
                    {
                        'ok': False,
                        'error': {
                            'kind': 'config',
                            'message': 'Dropbox tool arguments are invalid',
                        },
                    },
                )
        self.assertEqual(client.mock_calls, [])

    def test_direct_validator_matches_schema_for_json_like_values(self) -> None:
        """Match published schema semantics without raising on nested JSON values."""
        functions = {fn.name: fn for fn in DropboxTool().functions(_make_ctx())}
        cases: tuple[tuple[str, Any], ...] = (
            ('search', {'root': 'shared', 'query': '', 'kinds': []}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [{}]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [[]]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [True]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': [1]}),
            ('search', {'root': 'shared', 'query': 'x', 'kinds': {'file': True}}),
            ('list_folder', {'root': ''}),
            ('list_folder', {'root': 'shared', 'folder_ref': ''}),
            ('get_metadata', {'root': 'shared', 'item_ref': ''}),
        )
        for function, arguments in cases:
            expected = _schema_accepts(functions[function].parameters, arguments)
            with self.subTest(function=function, arguments=arguments):
                self.assertEqual(_valid_arguments(function, arguments), expected)

        client = MagicMock()
        client.search.return_value = {'items': [], 'next_cursor': None}
        invoke = DropboxTool().bind(
            _make_ctx(client_factory=cast(Callable[..., DropboxClient], lambda **_kwargs: client)),
            ToolInstance(id='files', type='dropbox', config=_CONFIG),
        )
        self.assertEqual(invoke('search', {'root': 'shared', 'query': ''}), client.search.return_value)
        client.search.assert_called_once_with(
            root='shared',
            query='',
            kinds=(),
            cursor=None,
            max_results=50,
        )

    def test_maps_all_typed_failures_to_common_kinds(self) -> None:
        """Normalize every Dropbox client failure without exposing provider details."""
        cases = (
            (DropboxAuthError('safe auth failure'), 'auth'),
            (DropboxForbiddenError('safe forbidden failure'), 'forbidden'),
            (DropboxOutsideRootError('safe root failure'), 'outside_root'),
            (DropboxNotFoundError('safe missing failure'), 'not_found'),
            (DropboxRateLimitedError('safe quota failure'), 'rate_limited'),
            (DropboxInvalidCursorError('safe cursor failure'), 'invalid_cursor'),
            (DropboxConfigError('safe config failure'), 'config'),
            (DropboxAPIError('safe api failure'), 'api'),
        )
        for failure, expected_kind in cases:
            with self.subTest(kind=expected_kind):
                client = MagicMock()
                client.list_roots.side_effect = failure
                factory = MagicMock(return_value=client)
                invoke = DropboxTool().bind(
                    _make_ctx(client_factory=cast(Callable[..., DropboxClient], factory)),
                    ToolInstance(id='files', type='dropbox', config=_CONFIG),
                )

                result = invoke('list_roots', {})

                self.assertEqual(
                    result,
                    {'ok': False, 'error': {'kind': expected_kind, 'message': str(failure)}},
                )

    def test_supplier_lookup_failures_map_to_safe_lazy_auth_result(self) -> None:
        """Normalize key-store lookup failures without leaking credential metadata."""
        failures = (
            KeyNotFoundError('credential not found: confidential-work-dropbox'),
            KeyTypeMismatchError(
                "key_ref 'confidential-work-dropbox' is type clickup, expected dropbox",
            ),
        )
        for failure in failures:
            with self.subTest(failure_type=type(failure).__name__):
                supplier = MagicMock(side_effect=failure)
                invoke = DropboxTool().bind(
                    _make_ctx(
                        secret_supplier_factory=MagicMock(return_value=supplier),
                        client_factory=cast(Callable[..., DropboxClient], _SupplierResolvingClient),
                    ),
                    ToolInstance(
                        id='files',
                        type='dropbox',
                        credential_ref='confidential-work-dropbox',
                        config=_CONFIG,
                    ),
                )
                supplier.assert_not_called()

                result = invoke('list_roots', {})

                self.assertEqual(
                    result,
                    {
                        'ok': False,
                        'error': {
                            'kind': 'auth',
                            'message': 'Dropbox credential could not be resolved',
                        },
                    },
                )
                self.assertNotIn('confidential-work-dropbox', str(result))
                self.assertNotIn('clickup', str(result))
                supplier.assert_called_once_with()

    def test_invalid_roots_return_config_failure_without_client(self) -> None:
        """Reject missing, empty, and malformed roots before constructing a client."""
        invalid_configs: tuple[dict[str, Any], ...] = (
            {},
            {'roots': []},
            {'roots': [{'id': '', 'path': '/Shared'}]},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                factory = MagicMock()
                invoke = DropboxTool().bind(
                    _make_ctx(client_factory=cast(Callable[..., DropboxClient], factory)),
                    ToolInstance(id='files', type='dropbox', config=config),
                )

                result = invoke('list_roots', {})

                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['kind'], 'config')
                factory.assert_not_called()
