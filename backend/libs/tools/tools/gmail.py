# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Gmail tool: map LLM-visible functions to GmailClient methods.

The full surface (including send/trash) is exposed; per-instance allow/deny gates it
(deny send/trash in example configs). Client ``GmailError``s are mapped to a uniform
``{ok, error}`` failure result shared with other integration tools.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from libs.clients.gmail.client import GmailClient
from libs.clients.gmail.errors import GmailAuthError, GmailError, GmailNotFoundError
from libs.clients.gmail.protocol import GmailClientProtocol
from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext, token_supplier_for

if TYPE_CHECKING:
    from libs.agent_spec.spec import ToolInstance

_MESSAGE_ID_DESC = 'Gmail message id (from `list`/queue item `ref.resource_id`).'


def _failure(exc: GmailError) -> dict[str, Any]:
    """Map a GmailError to a uniform tool failure result."""
    if isinstance(exc, GmailNotFoundError):
        kind = 'not_found'
    elif isinstance(exc, GmailAuthError):
        kind = 'auth'
    else:
        kind = 'api'
    return {'ok': False, 'error': {'kind': kind, 'message': str(exc)}}


class GmailTool(Tool):
    name = 'gmail'
    credential_type = 'google'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Return an invoke closed over a per-mailbox GmailClient.

        Credentials come from ``ctx.secret_supplier_factory``; per-instance
        config and client factory overrides from ``instance`` / ``ctx``.
        """
        config = instance.config if instance else {}
        token_supplier = token_supplier_for(
            ctx,
            credential_type=self.credential_type,
            credential_ref=instance.credential_ref if instance else None,
        )
        client_factory = ctx.client_factories.get(self.name)
        factory: Callable[..., GmailClientProtocol] = client_factory or GmailClient
        client = factory(token_supplier=token_supplier, config=config)

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            try:
                return self._dispatch(client, function, arguments)
            except GmailError as exc:
                return _failure(exc)

        return invoke

    def _dispatch(self, client: GmailClientProtocol, function: str, arguments: dict[str, Any]) -> Any:
        """Route one function call to the matching client method."""
        if function == 'list':
            return client.list_messages(
                query=arguments['query'],
                max_results=arguments.get('max_results', 100),
                page_token=arguments.get('page_token'),
            )
        if function == 'read':
            return client.get_message(arguments['message_id'], fmt='full')
        if function == 'list_labels':
            return {'labels': client.list_labels()}
        if function == 'get_attachment':
            return client.get_attachment(arguments['message_id'], arguments['attachment_id'])
        if function == 'label':
            add_ids = list(arguments.get('add', []))
            add_names = arguments.get('add_names', [])
            if add_names:
                add_ids.extend(client.ensure_label_ids(tuple(add_names)))
            return {
                'ok': True,
                **client.modify_labels(
                    arguments['message_id'],
                    add=tuple(add_ids),
                    remove=tuple(arguments.get('remove', [])),
                ),
            }
        if function == 'archive':
            return {'ok': True, **client.archive(arguments['message_id'])}
        if function == 'mark_spam':
            return {'ok': True, **client.report_spam(arguments['message_id'])}
        if function == 'trash':
            return {'ok': True, **client.trash(arguments['message_id'])}
        if function == 'send':
            return {'ok': True, **client.send_message(**arguments)}
        raise ValueError(f'Unknown function {function!r} on tool {self.name!r}')

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        """LLM-visible Gmail functions (handlers require ``bind``)."""
        msg_only = {
            'type': 'object',
            'properties': {'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC}},
            'required': ['message_id'],
        }
        return [
            ToolFunction(
                'list',
                'Search messages by Gmail query.',
                {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string', 'description': 'Gmail search query, e.g. "in:inbox".'},
                        'max_results': {'type': 'integer'},
                        'page_token': {'type': 'string'},
                    },
                    'required': ['query'],
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction('read', 'Read one message (full body).', msg_only, self._unbound, readonly=True),
            ToolFunction(
                'list_labels',
                'List label id/name pairs.',
                {'type': 'object', 'properties': {}, 'required': []},
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'get_attachment',
                'Download an attachment (base64).',
                {
                    'type': 'object',
                    'properties': {
                        'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC},
                        'attachment_id': {'type': 'string'},
                    },
                    'required': ['message_id', 'attachment_id'],
                },
                self._unbound,
                readonly=True,
            ),
            ToolFunction(
                'label',
                'Add/remove label ids on a message; ``add_names`` creates user labels when missing.',
                {
                    'type': 'object',
                    'properties': {
                        'message_id': {'type': 'string', 'description': _MESSAGE_ID_DESC},
                        'add': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Existing label ids.'},
                        'add_names': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Human label names to create (if needed) and apply.',
                        },
                        'remove': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['message_id'],
                },
                self._unbound,
                readonly=False,
            ),
            ToolFunction('archive', 'Archive (remove INBOX label).', msg_only, self._unbound, readonly=False),
            ToolFunction('mark_spam', 'Move message to spam.', msg_only, self._unbound, readonly=False),
            ToolFunction('trash', 'Move message to trash (deny by default).', msg_only, self._unbound, readonly=False),
            ToolFunction(
                'send',
                'Send a message (deny by default).',
                {
                    'type': 'object',
                    'properties': {
                        'to': {'type': 'string'},
                        'subject': {'type': 'string'},
                        'body': {'type': 'string'},
                    },
                    'required': ['to', 'subject', 'body'],
                },
                self._unbound,
                readonly=False,
            ),
        ]

    @staticmethod
    def _unbound(**_kwargs: Any) -> Any:
        raise RuntimeError('gmail tool requires bind(token_supplier=..., config=...)')
