# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for GmailTool (client stubbed)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.gmail import GmailClient
from libs.clients.gmail.errors import GmailAPIError, GmailAuthError, GmailNotFoundError
from libs.tools.context import ToolContext
from libs.tools.tools.gmail import GmailTool

from olib.py.django.test.cases import OTestCase


class _FakeGmailClient:
    """Records calls and returns canned data / raises on a sentinel id."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def list_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('list_messages', (), kwargs))
        return {'message_ids': ['m1'], 'next_page_token': None}

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        self.calls.append(('get_message', (message_id,), {'fmt': fmt}))
        if message_id == 'missing':
            raise GmailNotFoundError('no such message')
        return {
            'id': message_id,
            'threadId': 't1',
            'historyId': 'provider-history',
            'snippet': 'hi',
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'From', 'value': 'Alice <alice@example.com>'},
                    {'name': 'Subject', 'value': 'Compact'},
                ],
                'body': {'data': base64.urlsafe_b64encode(b'Full body').decode().rstrip('=')},
            },
        }

    def list_labels(self) -> list[dict[str, Any]]:
        """Return provider-shaped labels, including one unsupported field."""
        self.calls.append(('list_labels', (), {}))
        return [{'id': 'INBOX', 'name': 'Inbox', 'type': 'system', 'messagesTotal': 10}]

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Return the decoded-byte client attachment contract."""
        self.calls.append(('get_attachment', (message_id, attachment_id), {}))
        return {
            'attachment_id': attachment_id,
            'size': 999,
            'mime_type': 'text/plain',
            'data': b'attachment bytes',
        }

    def modify_labels(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(('modify_labels', (message_id,), kwargs))
        return {'historyId': 'provider-history', 'labelIds': ['STARRED']}

    def ensure_label_ids(self, names: tuple[str, ...]) -> list[str]:
        self.calls.append(('ensure_label_ids', (names,), {}))
        return ['L-new']

    def archive(self, message_id: str) -> dict[str, Any]:
        self.calls.append(('archive', (message_id,), {}))
        return {'id': 'provider-archive-id', 'historyId': 'provider-history'}

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Return a provider mutation response for spam."""
        self.calls.append(('report_spam', (message_id,), {}))
        return {'historyId': 'provider-history'}

    def trash(self, message_id: str) -> dict[str, Any]:
        """Return a provider mutation response for trash."""
        self.calls.append(('trash', (message_id,), {}))
        return {'id': message_id, 'historyId': 'provider-history'}

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        """Return the provider-assigned id for a sent message."""
        self.calls.append(('send_message', (), kwargs))
        return {'id': 'sent-provider-id', 'threadId': 'sent-thread', 'historyId': 'provider-history'}


class TestGmailTool(OTestCase):
    def _bound(self, fake: _FakeGmailClient) -> Callable[[str, dict[str, Any]], Any]:
        tool = GmailTool()
        inst = ToolInstance(id='gmail', type='gmail', config={'subject': 'me@example.com'})
        ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: '{"sa": true}',
            client_factories={'gmail': cast(Callable[..., GmailClient], lambda **kw: fake)},
        )
        return tool.bind(ctx, inst)

    def test_functions_expose_full_surface_with_readonly_flags(self) -> None:
        ctx = ToolContext(spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'), user_id=1)
        fns = {f.name: f for f in GmailTool().functions(ctx)}
        self.assertEqual(
            set(fns),
            {'list', 'read', 'list_labels', 'get_attachment', 'label', 'archive', 'mark_spam', 'trash', 'send'},
        )
        self.assertTrue(fns['list'].readonly)
        self.assertTrue(fns['read'].readonly)
        self.assertFalse(fns['archive'].readonly)
        self.assertFalse(fns['send'].readonly)

    def test_uses_shared_google_credential_type(self) -> None:
        self.assertEqual(GmailTool.credential_type, 'google')
        self.assertEqual(GmailTool.name, 'gmail')

    @patch('libs.clients.gmail.client._build_service')
    def test_real_tool_client_normalizes_subject_without_mutating_config(self, build_service: MagicMock) -> None:
        """Strip the delegated subject at the client boundary used by Gmail tools."""
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {'messages': []}
        build_service.return_value = service
        config = {'subject': ' user@example.com '}
        instance = ToolInstance(id='gmail', type='gmail', config=config)
        context = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: '{"type":"service_account"}',
        )

        invoke = GmailTool().bind(context, instance)
        invoke('list', {'query': 'in:inbox'})

        build_service.assert_called_once_with('{"type":"service_account"}', 'user@example.com')
        self.assertEqual(config['subject'], ' user@example.com ')
        service.close.assert_called_once_with()

    def test_list_maps_to_client(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out, {'message_ids': ['m1'], 'next_page_token': None})
        self.assertEqual(fake.calls[0][0], 'list_messages')

    def test_read_projects_full_message_without_provider_payload(self) -> None:
        """Expose decoded body content while dropping provider MIME and history records."""
        fake = _FakeGmailClient()
        invoke = self._bound(fake)

        out = invoke('read', {'message_id': 'm1'})

        self.assertEqual(out['body'], {'text': 'Full body', 'source': 'plain'})
        self.assertEqual(out['subject'], 'Compact')
        self.assertNotIn('payload', out)
        self.assertNotIn('historyId', out)
        self.assertEqual(fake.calls[0], ('get_message', ('m1',), {'fmt': 'full'}))

    def test_labels_and_attachment_use_exact_compact_contracts(self) -> None:
        """Project label identity and decoded attachment bytes to locked records."""
        fake = _FakeGmailClient()
        invoke = self._bound(fake)

        labels = invoke('list_labels', {})
        attachment = invoke('get_attachment', {'message_id': 'm1', 'attachment_id': 'a1'})

        self.assertEqual(labels, {'labels': [{'id': 'INBOX', 'name': 'Inbox', 'type': 'system'}]})
        self.assertEqual(
            attachment,
            {
                'attachment_id': 'a1',
                'size': 16,
                'mime_type': 'text/plain',
                'data_base64': 'YXR0YWNobWVudCBieXRlcw==',
            },
        )

    def test_mutations_return_compact_ack_with_provider_or_caller_id(self) -> None:
        """Drop provider transport fields and preserve the best message identity."""
        fake = _FakeGmailClient()
        invoke = self._bound(fake)

        self.assertEqual(
            invoke('label', {'message_id': 'label-caller', 'add': ['STARRED']}),
            {'ok': True, 'message_id': 'label-caller', 'label_ids': ['STARRED']},
        )
        self.assertEqual(
            invoke('archive', {'message_id': 'archive-caller'}),
            {'ok': True, 'message_id': 'provider-archive-id'},
        )
        self.assertEqual(
            invoke('mark_spam', {'message_id': 'spam-caller'}),
            {'ok': True, 'message_id': 'spam-caller'},
        )
        self.assertEqual(
            invoke('trash', {'message_id': 'trash-caller'}),
            {'ok': True, 'message_id': 'trash-caller'},
        )
        self.assertEqual(
            invoke('send', {'to': 'bob@example.com', 'subject': 'Hi', 'body': 'Hello'}),
            {'ok': True, 'message_id': 'sent-provider-id'},
        )

    def test_not_found_maps_to_failure_result(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('read', {'message_id': 'missing'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'not_found')

    def test_auth_failure_maps_to_uniform_safe_envelope(self) -> None:
        """Map typed authentication failures without exposing attached provider state."""

        class _AuthFailureClient(_FakeGmailClient):
            def list_messages(self, **kwargs: Any) -> dict[str, Any]:
                """Raise a safe typed failure carrying private diagnostic state."""
                del kwargs
                failure = GmailAuthError('Gmail authorization failed')
                failure.private_response = {'raw': 'private-auth-response'}  # type: ignore[attr-defined]
                raise failure

        out = self._bound(_AuthFailureClient())('list', {'query': 'in:inbox'})

        self.assertEqual(
            out,
            {'ok': False, 'error': {'kind': 'auth', 'message': 'Gmail authorization failed'}},
        )
        self.assertNotIn('private-auth-response', repr(out))

    def test_api_failure_maps_to_uniform_safe_envelope(self) -> None:
        """Map generic API failures without exposing status or private provider data."""

        class _APIFailureClient(_FakeGmailClient):
            def list_labels(self) -> list[dict[str, Any]]:
                """Raise a safe API failure carrying non-contract diagnostics."""
                failure = GmailAPIError('Gmail request failed', status=503)
                failure.private_response = {'raw': 'private-api-response'}  # type: ignore[attr-defined]
                raise failure

        out = self._bound(_APIFailureClient())('list_labels', {})

        self.assertEqual(
            out,
            {'ok': False, 'error': {'kind': 'api', 'message': 'Gmail request failed'}},
        )
        self.assertNotIn('503', repr(out))
        self.assertNotIn('private-api-response', repr(out))

    def test_projection_failure_maps_to_uniform_safe_envelope(self) -> None:
        """Map malformed successful payloads without returning any raw attachment fields."""

        class _MalformedAttachmentClient(_FakeGmailClient):
            def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
                """Return malformed decoded content containing a private raw sentinel."""
                del message_id
                return {
                    'attachment_id': attachment_id,
                    'mime_type': 'text/plain',
                    'data': 'private-raw-attachment',
                    'raw': {'provider': 'private-provider-payload'},
                }

        out = self._bound(_MalformedAttachmentClient())(
            'get_attachment',
            {'message_id': 'm1', 'attachment_id': 'a1'},
        )

        self.assertEqual(
            out,
            {'ok': False, 'error': {'kind': 'api', 'message': 'Invalid Gmail attachment response'}},
        )
        self.assertNotIn('private-raw-attachment', repr(out))
        self.assertNotIn('private-provider-payload', repr(out))

    def test_label_add_names_resolves_via_ensure_label_ids(self) -> None:
        fake = _FakeGmailClient()
        invoke = self._bound(fake)
        out = invoke('label', {'message_id': 'm1', 'add_names': ['x-act']})
        self.assertEqual(out, {'ok': True, 'message_id': 'm1', 'label_ids': ['STARRED']})
        self.assertEqual(fake.calls[0][0], 'ensure_label_ids')

    def test_attachment_description_promises_decoded_base64(self) -> None:
        """Describe the projected encoding rather than the provider wire format."""
        ctx = ToolContext(spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'), user_id=1)
        functions = {function.name: function for function in GmailTool().functions(ctx)}

        self.assertEqual(functions['get_attachment'].description, 'Download one attachment as decoded base64.')
