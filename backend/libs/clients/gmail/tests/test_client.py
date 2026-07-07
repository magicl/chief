# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for GmailClient (Gmail service faked via MagicMock)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from googleapiclient.errors import HttpError
from libs.clients.gmail.client import GmailClient
from libs.clients.gmail.errors import GmailAPIError, GmailAuthError, GmailNotFoundError

from olib.py.django.test.cases import OTestCase


def _client_with_service(service: MagicMock, *, token: str | None = '{"sa": true}') -> GmailClient:
    """Build a GmailClient whose service factory returns the supplied fake service."""
    return GmailClient(
        token_supplier=lambda: token,
        config={'subject': 'me@example.com'},
        service_factory=lambda raw, subject: service,
    )


def _http_failure(status: int) -> HttpError:
    """Build a minimal HttpError for mapping tests."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b'failure')


class TestGmailClient(OTestCase):
    def test_list_messages_parses_ids_and_page_token(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'm1'}, {'id': 'm2'}],
            'nextPageToken': 'tok',
        }
        client = _client_with_service(service)
        out = client.list_messages(query='in:inbox', max_results=25)
        self.assertEqual(out['message_ids'], ['m1', 'm2'])
        self.assertEqual(out['next_page_token'], 'tok')
        service.users.return_value.messages.return_value.list.assert_called_once_with(
            userId='me', q='in:inbox', maxResults=25, pageToken=None
        )

    def test_list_messages_handles_empty(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        client = _client_with_service(service)
        out = client.list_messages(query='in:inbox')
        self.assertEqual(out['message_ids'], [])
        self.assertIsNone(out['next_page_token'])

    def test_list_message_ids_paginates_until_max_results(self) -> None:
        service = MagicMock()
        list_mock = service.users.return_value.messages.return_value.list
        list_mock.return_value.execute.side_effect = [
            {'messages': [{'id': 'm1'}, {'id': 'm2'}], 'nextPageToken': 'p2'},
            {'messages': [{'id': 'm3'}], 'nextPageToken': None},
        ]
        client = _client_with_service(service)
        ids = client.list_message_ids(query='in:inbox', max_results=3)
        self.assertEqual(ids, ['m1', 'm2', 'm3'])
        self.assertEqual(list_mock.call_count, 2)

    def test_archive_removes_inbox_label(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {'id': 'm1'}
        client = _client_with_service(service)
        client.archive('m1')
        service.users.return_value.messages.return_value.modify.assert_called_once_with(
            userId='me', id='m1', body={'addLabelIds': [], 'removeLabelIds': ['INBOX']}
        )

    def test_report_spam_adds_spam_removes_inbox(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {'id': 'm1'}
        client = _client_with_service(service)
        client.report_spam('m1')
        service.users.return_value.messages.return_value.modify.assert_called_once_with(
            userId='me', id='m1', body={'addLabelIds': ['SPAM'], 'removeLabelIds': ['INBOX']}
        )

    def test_trash_moves_message(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.trash.return_value.execute.return_value = {'id': 'm1'}
        client = _client_with_service(service)
        out = client.trash('m1')
        self.assertEqual(out['id'], 'm1')
        service.users.return_value.messages.return_value.trash.assert_called_once_with(userId='me', id='m1')

    def test_send_message_posts_raw_mime(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.send.return_value.execute.return_value = {'id': 'sent1'}
        client = _client_with_service(service)
        out = client.send_message(to='bob@example.com', subject='Hi', body='Hello')
        self.assertEqual(out['id'], 'sent1')
        send_call = service.users.return_value.messages.return_value.send.call_args
        raw = send_call.kwargs['body']['raw']
        self.assertTrue(isinstance(raw, str) and len(raw) > 0)

    def test_get_attachment_decodes_bytes(self) -> None:
        service = MagicMock()
        payload = base64.urlsafe_b64encode(b'pdf-bytes').decode().rstrip('=')
        service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
            'data': payload,
            'size': 9,
        }
        client = _client_with_service(service)
        out = client.get_attachment('m1', 'att1')
        self.assertEqual(out['data'], b'pdf-bytes')
        self.assertEqual(out['attachment_id'], 'att1')

    def test_create_label_and_ensure_label_ids(self) -> None:
        service = MagicMock()
        service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
            'labels': [{'id': 'L1', 'name': 'existing'}]
        }
        service.users.return_value.labels.return_value.create.return_value.execute.return_value = {
            'id': 'L2',
            'name': 'x-act',
        }
        client = _client_with_service(service)
        created = client.create_label('x-act')
        self.assertEqual(created['id'], 'L2')
        ids = client.ensure_label_ids(('existing', 'x-act'))
        self.assertEqual(ids, ['L1', 'L2'])

    def test_missing_subject_raises_auth_failure(self) -> None:
        client = GmailClient(
            token_supplier=lambda: '{"sa": true}',
            config={},
            service_factory=lambda raw, subject: MagicMock(),
        )
        with self.assertRaises(GmailAuthError):
            client.list_messages(query='in:inbox')

    def test_missing_credential_raises_auth_failure(self) -> None:
        client = _client_with_service(MagicMock(), token=None)
        with self.assertRaises(GmailAuthError):
            client.list_messages(query='in:inbox')

    def test_http_404_maps_to_not_found(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = _http_failure(404)
        client = _client_with_service(service)
        with self.assertRaises(GmailNotFoundError):
            client.get_message('missing')

    def test_http_403_maps_to_auth_failure(self) -> None:
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = _http_failure(403)
        client = _client_with_service(service)
        with self.assertRaises(GmailAuthError):
            client.get_message('m1')

    def test_oversized_attachment_raises_api_failure(self) -> None:
        service = MagicMock()
        huge = base64.urlsafe_b64encode(b'x' * (11 * 1024 * 1024)).decode().rstrip('=')
        service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
            'data': huge,
            'size': 11 * 1024 * 1024,
        }
        client = _client_with_service(service)
        with self.assertRaises(GmailAPIError):
            client.get_attachment('m1', 'att-big')
