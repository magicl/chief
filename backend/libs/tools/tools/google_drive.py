# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Google Drive tool exposing root-safe metadata operations only."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from libs.clients.google_drive.client import GoogleDriveClient
from libs.clients.google_drive.config import parse_google_drive_config
from libs.clients.google_drive.errors import (
    GoogleDriveAPIError,
    GoogleDriveAuthError,
    GoogleDriveConfigError,
    GoogleDriveError,
    GoogleDriveForbiddenError,
    GoogleDriveInvalidCursorError,
    GoogleDriveNotFoundError,
    GoogleDriveOutsideRootError,
    GoogleDriveRateLimitedError,
)
from libs.clients.google_drive.protocol import GoogleDriveClientProtocol
from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext, token_supplier_for

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

_MAX_RESULTS_SCHEMA = {
    'type': 'integer',
    'default': 50,
    'minimum': 1,
    'maximum': 100,
}
_MAX_ROOT_ALIAS_LENGTH = 256
_MAX_ITEM_REF_LENGTH = 4_096
_MAX_QUERY_LENGTH = 4_096
_MAX_CURSOR_LENGTH = 131_072
_REQUIRED_ARGUMENTS = {
    'list_roots': (),
    'list_folder': ('root',),
    'get_metadata': ('root', 'item_ref'),
    'search': ('root', 'query'),
}
_ARGUMENT_FIELDS = {
    'list_roots': frozenset(),
    'list_folder': frozenset({'root', 'folder_ref', 'cursor', 'max_results'}),
    'get_metadata': frozenset({'root', 'item_ref'}),
    'search': frozenset({'root', 'query', 'kinds', 'cursor', 'max_results'}),
}


def _failure(exc: GoogleDriveError) -> dict[str, Any]:
    """Map a typed Drive failure to the common integration-tool result."""
    mappings = (
        (GoogleDriveAuthError, 'auth'),
        (GoogleDriveForbiddenError, 'forbidden'),
        (GoogleDriveOutsideRootError, 'outside_root'),
        (GoogleDriveNotFoundError, 'not_found'),
        (GoogleDriveRateLimitedError, 'rate_limited'),
        (GoogleDriveInvalidCursorError, 'invalid_cursor'),
        (GoogleDriveConfigError, 'config'),
        (GoogleDriveAPIError, 'api'),
    )
    kind = next((name for failure_type, name in mappings if isinstance(exc, failure_type)), 'api')
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


def _safe_token_supplier(supplier: Callable[[], str | None]) -> Callable[[], str | None]:
    """Hide key lookup/type details behind a stable Drive authentication failure."""

    def resolve() -> str | None:
        """Resolve lazily while normalizing the key store's public lookup failures."""
        try:
            return supplier()
        # apps.keys KeyNotFoundError and KeyTypeMismatchError intentionally inherit
        # these stdlib bases; libs cannot import apps across the architecture boundary.
        except (LookupError, ValueError):
            raise GoogleDriveAuthError('Google Drive credential could not be resolved') from None

    return resolve


def _valid_arguments(function: Any, arguments: Any) -> bool:
    """Validate direct invocations as strictly as the published JSON schemas."""
    if not isinstance(function, str) or not isinstance(arguments, Mapping):
        return False
    required = _REQUIRED_ARGUMENTS.get(function)
    allowed = _ARGUMENT_FIELDS.get(function)
    if required is None or allowed is None or not set(arguments).issubset(allowed):
        return False
    if any(name not in arguments for name in required):
        return False

    string_limits = {
        'root': _MAX_ROOT_ALIAS_LENGTH,
        'folder_ref': _MAX_ITEM_REF_LENGTH,
        'item_ref': _MAX_ITEM_REF_LENGTH,
        'query': _MAX_QUERY_LENGTH,
        'cursor': _MAX_CURSOR_LENGTH,
    }
    for name, limit in string_limits.items():
        if name in arguments:
            value = arguments[name]
            if not isinstance(value, str) or (name != 'query' and not value) or len(value) > limit:
                return False
    if 'max_results' in arguments:
        value = arguments['max_results']
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            return False
    if 'kinds' in arguments:
        kinds = arguments['kinds']
        if (
            not isinstance(kinds, list)
            or len(kinds) > 2
            or any(not isinstance(kind, str) or kind not in {'file', 'folder'} for kind in kinds)
        ):
            return False
    return True


class GoogleDriveTool(Tool):
    """Expose root-safe Drive metadata operations to an agent."""

    name = 'google_drive'
    credential_type = 'google'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Bind one configured Drive instance to a lazy credential and client."""
        config = instance.config if instance else {}
        try:
            parse_google_drive_config(config)
        except GoogleDriveConfigError as exc:
            failure = _failure(exc)

            def invoke_invalid(_function: str, _arguments: dict[str, Any]) -> Any:
                """Return the bind-time configuration failure for every invocation."""
                return failure

            return invoke_invalid

        token_supplier = _safe_token_supplier(
            token_supplier_for(
                ctx,
                credential_type=self.credential_type,
                credential_ref=instance.credential_ref if instance else None,
            )
        )
        client_factory = ctx.client_factories.get(self.name)
        factory: Callable[..., GoogleDriveClientProtocol] = client_factory or GoogleDriveClient
        client = factory(
            token_supplier=token_supplier,
            config=config,
            instance_id=instance.id if instance else self.name,
        )

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            """Dispatch one call and normalize all typed client failures."""
            if not isinstance(function, str):
                return _failure(GoogleDriveConfigError('Google Drive tool arguments are invalid'))
            if function not in _REQUIRED_ARGUMENTS:
                return _failure(GoogleDriveConfigError('Unknown Google Drive tool function'))
            if not _valid_arguments(function, arguments):
                return _failure(GoogleDriveConfigError('Google Drive tool arguments are invalid'))
            try:
                return self._dispatch(client, function, arguments)
            except GoogleDriveError as exc:
                return _failure(exc)

        return invoke

    def _dispatch(
        self,
        client: GoogleDriveClientProtocol,
        function: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Route one validated tool function to the client protocol."""
        if function == 'list_roots':
            return client.list_roots()
        if function == 'list_folder':
            return client.list_folder(
                root=arguments['root'],
                folder_ref=arguments.get('folder_ref'),
                cursor=arguments.get('cursor'),
                max_results=arguments.get('max_results', 50),
            )
        if function == 'get_metadata':
            return client.get_metadata(
                root=arguments['root'],
                item_ref=arguments['item_ref'],
            )
        if function == 'search':
            return client.search(
                root=arguments['root'],
                query=arguments['query'],
                kinds=tuple(arguments.get('kinds', [])),
                cursor=arguments.get('cursor'),
                max_results=arguments.get('max_results', 50),
            )
        raise GoogleDriveConfigError('Unknown Google Drive tool function')

    def functions(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> list[ToolFunction]:
        """Return the matching four-function read-only metadata schema."""
        root = {
            'type': 'string',
            'minLength': 1,
            'maxLength': _MAX_ROOT_ALIAS_LENGTH,
            'description': 'Configured root alias that bounds all metadata access.',
        }
        return [
            ToolFunction(
                'list_roots',
                'List configured root metadata only; does not return file content.',
                {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'list_folder',
                'List direct child metadata only; does not return file content.',
                {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'folder_ref': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': _MAX_ITEM_REF_LENGTH,
                            'description': 'Folder item reference; defaults to the selected root.',
                        },
                        'cursor': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': _MAX_CURSOR_LENGTH,
                            'description': 'Opaque cursor from a prior matching call.',
                        },
                        'max_results': dict(_MAX_RESULTS_SCHEMA),
                    },
                    'required': ['root'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'get_metadata',
                'Get metadata for one authorized item; does not return file content.',
                {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'item_ref': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': _MAX_ITEM_REF_LENGTH,
                            'description': 'Drive item reference beneath the root.',
                        },
                    },
                    'required': ['root', 'item_ref'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'search',
                'Search authorized item metadata only; does not return file content.',
                {
                    'type': 'object',
                    'properties': {
                        'root': root,
                        'query': {
                            'type': 'string',
                            'maxLength': _MAX_QUERY_LENGTH,
                            'description': 'Native Drive metadata search text.',
                        },
                        'kinds': {
                            'type': 'array',
                            'maxItems': 2,
                            'items': {'type': 'string', 'enum': ['file', 'folder']},
                        },
                        'cursor': {
                            'type': 'string',
                            'minLength': 1,
                            'maxLength': _MAX_CURSOR_LENGTH,
                            'description': 'Opaque cursor from a prior matching call.',
                        },
                        'max_results': dict(_MAX_RESULTS_SCHEMA),
                    },
                    'required': ['root', 'query'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        """Reject direct handler use because Drive requires instance binding."""
        raise RuntimeError('google_drive tool requires bind(token_supplier=..., config=...)')
