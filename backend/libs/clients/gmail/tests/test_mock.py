# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for the in-memory Gmail test client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.gmail.mock import MockGmailClient
from libs.clients.gmail.protocol import GmailClientProtocol
from libs.tools.context import ToolContext
from libs.tools.tools.gmail import GmailTool

from olib.py.django.test.cases import OTestCase


def _invoke_with(client: MockGmailClient) -> Callable[[str, dict[str, Any]], Any]:
    """Bind GmailTool to a supplied mock client."""
    inst = ToolInstance(id='gmail', type='gmail', config={})
    ctx = ToolContext(
        spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
        client_factories={'gmail': lambda **_kwargs: client},
    )
    return GmailTool().bind(ctx, inst)


class TestMockGmailClient(OTestCase):
    def test_seeded_messages_can_be_listed_and_read_by_tool(self) -> None:
        client = MockGmailClient(token_supplier=lambda: None, config={'subject': 'me@example.com'})
        protocol_client: GmailClientProtocol = client
        assert protocol_client is client
        client.seed_message('m1', labels=('INBOX',), message={'snippet': 'hello'})
        client.seed_message('m2', labels=('SENT',), message={'snippet': 'sent'})
        invoke = _invoke_with(client)

        listing = invoke('list', {'query': 'in:inbox', 'max_results': 10})
        message = invoke('read', {'message_id': 'm1'})

        self.assertEqual(listing, {'message_ids': ['m1'], 'next_page_token': None})
        self.assertEqual(message['id'], 'm1')
        self.assertEqual(message['snippet'], 'hello')
        self.assertEqual(message['labelIds'], ['INBOX'])

    def test_label_names_create_synthetic_ids_and_record_mutation(self) -> None:
        client = MockGmailClient(token_supplier=lambda: None, config={})
        client.seed_message('m1', labels=('INBOX',))

        result = _invoke_with(client)(
            'label',
            {'message_id': 'm1', 'add_names': ['Follow Up'], 'remove': ['INBOX']},
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['labelIds'], ['Label_1'])
        self.assertEqual(client.ensure_label_ids(('Follow Up',)), ['Label_1'])
        self.assertEqual(client.labeled, [{'message_id': 'm1', 'add': ['Label_1'], 'remove': ['INBOX']}])

    def test_archive_and_spam_are_recorded(self) -> None:
        client = MockGmailClient(token_supplier=lambda: None, config={})
        client.seed_message('m1', labels=('INBOX',))
        client.seed_message('m2', labels=('INBOX',))
        invoke = _invoke_with(client)

        archive_result = invoke('archive', {'message_id': 'm1'})
        spam_result = invoke('mark_spam', {'message_id': 'm2'})

        self.assertTrue(archive_result['ok'])
        self.assertTrue(spam_result['ok'])
        self.assertEqual(client.archived, ['m1'])
        self.assertEqual(client.spam, ['m2'])
        self.assertEqual(client.get_message('m1')['labelIds'], [])
        self.assertEqual(client.get_message('m2')['labelIds'], ['SPAM'])

    def test_attachment_lookup_returns_seeded_payload(self) -> None:
        client = MockGmailClient(token_supplier=lambda: None, config={})
        client.seed_message(
            'm1',
            attachments={'att1': {'attachment_id': 'att1', 'data': b'payload', 'size': 7}},
        )

        attachment = _invoke_with(client)('get_attachment', {'message_id': 'm1', 'attachment_id': 'att1'})

        self.assertEqual(attachment, {'attachment_id': 'att1', 'data': b'payload', 'size': 7})
