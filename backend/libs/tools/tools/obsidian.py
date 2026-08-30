# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Obsidian vault tool: root-scoped list/read/write/append/status against one Sync vault.

Root scoping and the first-sync gate are enforced server-side by the vault
service (see `services/obsidian`); this tool only validates argument shape
before dispatch and normalizes typed client failures. For file operations,
`sync_pending` and `unavailable` are stall conditions the vault service
expects callers to retry, so those dispatches are wrapped in
`_call_with_retry`. `status` is the observation path and is never retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from libs.clients.obsidian.client import ObsidianVaultClient
from libs.clients.obsidian.config import ObsidianToolConfig, parse_obsidian_tool_config
from libs.clients.obsidian.errors import (
    ObsidianAuthError,
    ObsidianConfigError,
    ObsidianForbiddenError,
    ObsidianNotFoundError,
    ObsidianOutsideRootError,
    ObsidianSyncPendingError,
    ObsidianUnavailableError,
    ObsidianVaultError,
)
from libs.clients.obsidian.protocol import ObsidianVaultClientProtocol
from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

_MAX_PATH_LENGTH = 4_096
_MAX_CONTENT_LENGTH = 1_000_000
_PATH_DESC = 'Vault-relative path (must resolve within one of the configured roots).'
_ROOT_CAVEAT = "Path must resolve within one of the tool instance's configured roots."
_REQUIRED_ARGUMENTS = {
    'list': ('path',),
    'read': ('path',),
    'write': ('path', 'content'),
    'append': ('path', 'content'),
    'status': (),
}
_ARGUMENT_FIELDS = {
    'list': frozenset({'path'}),
    'read': frozenset({'path'}),
    'write': frozenset({'path', 'content'}),
    'append': frozenset({'path', 'content'}),
    'status': frozenset(),
}
# Geometric backoff summing to ~30s, matching the stall budget documented in
# docs/docs/agents.md's `obsidian` tool section: production retries a
# first-sync/unavailable stall for roughly half a minute before giving up.
_DEFAULT_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


def _failure(exc: ObsidianVaultError) -> dict[str, Any]:
    """Map a typed Obsidian vault failure to the common integration-tool result."""
    mappings = (
        (ObsidianAuthError, 'auth'),
        (ObsidianForbiddenError, 'forbidden'),
        (ObsidianOutsideRootError, 'outside_root'),
        (ObsidianNotFoundError, 'not_found'),
        (ObsidianSyncPendingError, 'sync_pending'),
        (ObsidianUnavailableError, 'unavailable'),
        (ObsidianConfigError, 'config'),
    )
    kind = next((name for failure_type, name in mappings if isinstance(exc, failure_type)), 'api')
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


def _call_with_retry(fn: Callable[[], Any], *, sleep: Callable[[float], None], delays: tuple[float, ...]) -> Any:
    """Call ``fn()``, retrying on retryable vault stalls per the given delay schedule.

    ``sync_pending`` (first sync not complete) and ``unavailable`` (transient vault
    service trouble) are expected to clear on their own; every other typed failure
    propagates immediately. The attempt after the schedule is exhausted is not
    wrapped, so a still-failing vault surfaces its real error to the caller instead
    of retrying forever.
    """
    for delay in delays:
        try:
            return fn()
        except (ObsidianSyncPendingError, ObsidianUnavailableError):
            sleep(delay)
    return fn()


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

    if 'path' in allowed:
        path = arguments['path']
        if not isinstance(path, str) or not path or len(path) > _MAX_PATH_LENGTH:
            return False
    if 'content' in arguments:
        content = arguments['content']
        if not isinstance(content, str) or len(content) > _MAX_CONTENT_LENGTH:
            return False
    return True


class ObsidianTool(Tool):
    """Expose root-scoped Obsidian vault file operations and first-sync status to an agent."""

    name = 'obsidian'
    credential_type = 'obsidian'

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS,
    ) -> None:
        """Retain the retryable-stall backoff schedule; tests inject a fake sleep and short delays."""
        self._sleep = sleep
        self._delays = delays

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Bind one configured Obsidian vault instance to a lazily-validated client."""
        raw_config = instance.config if instance else {}
        try:
            config = parse_obsidian_tool_config(raw_config)
            client = self._build_client(ctx, instance)
        except ObsidianConfigError as exc:
            failure = _failure(exc)

            def invoke_invalid(_function: str, _arguments: dict[str, Any]) -> Any:
                """Return the bind-time configuration failure for every invocation."""
                return failure

            return invoke_invalid

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            """Dispatch one call and normalize all typed client failures."""
            if not isinstance(function, str):
                return _failure(ObsidianConfigError('Obsidian tool arguments are invalid'))
            if function not in _REQUIRED_ARGUMENTS:
                return _failure(ObsidianConfigError('Unknown Obsidian tool function'))
            if not _valid_arguments(function, arguments):
                return _failure(ObsidianConfigError('Obsidian tool arguments are invalid'))
            try:
                return self._dispatch(client, config, function, arguments)
            except ObsidianVaultError as exc:
                return _failure(exc)

        return invoke

    def _build_client(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None,
    ) -> ObsidianVaultClientProtocol:
        """Return the test-injected client factory's client, or a default built from settings.

        The default factory requires ``ctx.agent_id`` (every vault operation is scoped to
        one agent by the vault service); an injected factory is trusted to build a usable
        client on its own, since tests may not need a real agent id.
        """
        agent_id = str(ctx.agent_id) if ctx.agent_id is not None else None
        client_factory = ctx.client_factories.get(self.name)
        if client_factory is not None:
            return cast(
                ObsidianVaultClientProtocol,
                client_factory(
                    agent_id=agent_id,
                    config=instance.config if instance else {},
                    instance_id=instance.id if instance else self.name,
                ),
            )
        if agent_id is None:
            raise ObsidianConfigError('obsidian tool requires an agent id')

        from django.conf import settings

        return ObsidianVaultClient(
            base_url=settings.OBSIDIAN_VAULT_URL,
            token=settings.OBSIDIAN_VAULT_TOKEN,
            agent_id=agent_id,
        )

    def _dispatch(
        self,
        client: ObsidianVaultClientProtocol,
        config: ObsidianToolConfig,
        function: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Route one validated tool function to the client protocol.

        File operations retry vault stalls; ``status`` is a single shot so the
        agent can observe not-ready without sleeping the session.
        """
        if function == 'status':
            body = client.get_status(vault_id=config.vault)
            return {'ok': True, **body}
        if function == 'list':
            entries = _call_with_retry(
                lambda: client.list_dir(vault_id=config.vault, path=arguments['path']),
                sleep=self._sleep,
                delays=self._delays,
            )
            return {'ok': True, 'entries': entries}
        if function == 'read':
            content = _call_with_retry(
                lambda: client.read_text(vault_id=config.vault, path=arguments['path']),
                sleep=self._sleep,
                delays=self._delays,
            )
            return {'ok': True, 'content': content}
        if function == 'write':
            _call_with_retry(
                lambda: client.write_text(vault_id=config.vault, path=arguments['path'], content=arguments['content']),
                sleep=self._sleep,
                delays=self._delays,
            )
            return {'ok': True}
        if function == 'append':
            _call_with_retry(
                lambda: client.append_text(
                    vault_id=config.vault,
                    path=arguments['path'],
                    content=arguments['content'],
                ),
                sleep=self._sleep,
                delays=self._delays,
            )
            return {'ok': True}
        raise ObsidianConfigError('Unknown Obsidian tool function')

    def functions(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> list[ToolFunction]:
        """Return the matching root-scoped file schema plus readonly status."""
        path_schema = {
            'type': 'string',
            'minLength': 1,
            'maxLength': _MAX_PATH_LENGTH,
            'description': _PATH_DESC,
        }
        content_schema = {
            'type': 'string',
            'maxLength': _MAX_CONTENT_LENGTH,
            'description': 'UTF-8 markdown/text content.',
        }
        return [
            ToolFunction(
                'list',
                f'List direct child entry names under a vault directory. {_ROOT_CAVEAT}',
                {
                    'type': 'object',
                    'properties': {'path': path_schema},
                    'required': ['path'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'read',
                f'Read the UTF-8 text content of one vault file. {_ROOT_CAVEAT}',
                {
                    'type': 'object',
                    'properties': {'path': path_schema},
                    'required': ['path'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'write',
                f'Create or overwrite one vault file with new content. {_ROOT_CAVEAT}',
                {
                    'type': 'object',
                    'properties': {'path': path_schema, 'content': content_schema},
                    'required': ['path', 'content'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction(
                'append',
                f'Append content to one vault file, creating it (and parent dirs) if missing. {_ROOT_CAVEAT}',
                {
                    'type': 'object',
                    'properties': {'path': path_schema, 'content': content_schema},
                    'required': ['path', 'content'],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction(
                'status',
                (
                    'Report first-sync readiness and whether continuous headless Sync is alive '
                    'for the configured vault. Not a live "caught up" indicator from Obsidian Sync.'
                ),
                {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                    'additionalProperties': False,
                },
                self._unbound,
                readonly=True,
            ),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        """Reject direct handler use because Obsidian requires instance binding."""
        raise RuntimeError('obsidian tool requires bind(...)')
