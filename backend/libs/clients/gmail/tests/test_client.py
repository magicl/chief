# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for GmailClient (Gmail service faked via MagicMock)."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.errors import HttpError
from libs.clients.gmail.client import SCOPES, GmailClient, _build_service
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


def _gmail_traceback_locals(failure: BaseException) -> list[tuple[str, dict[str, Any]]]:
    """Collect locals retained by Gmail client frames in a failure traceback."""
    retained: list[tuple[str, dict[str, Any]]] = []
    traceback = failure.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals.get('__name__') == 'libs.clients.gmail.client':
            retained.append((traceback.tb_frame.f_code.co_name, dict(traceback.tb_frame.f_locals)))
        traceback = traceback.tb_next
    return retained


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

    def test_poll_metadata_reuses_one_service_and_closes_once(self) -> None:
        """Fetch a source batch with one credential, service, and deterministic close."""
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'm1'}, {'id': 'm2'}],
        }
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = [
            {'id': 'm1'},
            {'id': 'm2'},
        ]
        factory = MagicMock(return_value=service)
        client = GmailClient(
            token_supplier=lambda: '{"type":"service_account"}',
            config={'subject': 'user@example.com'},
            service_factory=factory,
        )
        poll_metadata = getattr(client, 'poll_message_metadata', None)
        self.assertIsNotNone(poll_metadata)
        assert callable(poll_metadata)

        with poll_metadata(query='in:inbox', max_results=25) as (message_ids, messages):
            streamed_messages = list(messages)
            self.assertEqual(message_ids, ['m1', 'm2'])
            self.assertEqual([message_id for message_id, _message in streamed_messages], ['m1', 'm2'])
            self.assertEqual([message['id'] for _message_id, message in streamed_messages], ['m1', 'm2'])
        factory.assert_called_once()
        service.close.assert_called_once_with()

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

    def test_oauth_without_subject_uses_me_for_read_and_send(self) -> None:
        """Allow OAuth operations without delegation and address the authorized user as me."""
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {'messages': []}
        service.users.return_value.messages.return_value.send.return_value.execute.return_value = {'id': 'sent'}
        envelope = json.dumps(
            {
                'chief_google_oauth': 1,
                'client_id': 'client-id',
                'client_secret': 'client-secret',
                'refresh_token': 'refresh-token',
                'scopes': [
                    'https://www.googleapis.com/auth/gmail.readonly',
                    'https://www.googleapis.com/auth/gmail.send',
                ],
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        )
        factory = MagicMock(return_value=service)
        client = GmailClient(
            token_supplier=lambda: envelope,
            config={},
            service_factory=factory,
        )
        client.list_messages(query='in:inbox')
        client.send_message(to='user@example.com', subject='Hello', body='Body')

        self.assertEqual(factory.call_args_list[0].args, (envelope, None))
        service.users.return_value.messages.return_value.list.assert_called_once_with(
            userId='me',
            q='in:inbox',
            maxResults=100,
            pageToken=None,
        )
        self.assertEqual(service.users.return_value.messages.return_value.send.call_args.kwargs['userId'], 'me')

    @patch('googleapiclient.discovery.build')
    def test_each_oauth_gmail_scope_reaches_real_credentials_and_me_requests(self, build: MagicMock) -> None:
        """Preserve each supported OAuth scope through real shared auth and Gmail addressing."""
        cases = (
            ('https://www.googleapis.com/auth/gmail.readonly', 'read'),
            ('https://www.googleapis.com/auth/gmail.modify', 'modify'),
            ('https://www.googleapis.com/auth/gmail.send', 'send'),
        )
        for scope, operation in cases:
            service = MagicMock()
            service.users.return_value.messages.return_value.list.return_value.execute.return_value = {'messages': []}
            service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {
                'id': 'message'
            }
            service.users.return_value.messages.return_value.send.return_value.execute.return_value = {'id': 'sent'}
            build.reset_mock()
            build.return_value = service
            envelope = json.dumps(
                {
                    'chief_google_oauth': 1,
                    'client_id': 'client-id',
                    'client_secret': 'client-secret',
                    'refresh_token': 'refresh-token',
                    'scopes': [scope],
                    'token_uri': 'https://oauth2.googleapis.com/token',
                }
            )

            def token_supplier(raw: str = envelope) -> str:
                """Return the envelope fixed for this scope subtest."""
                return raw

            client = GmailClient(token_supplier=token_supplier, config={})

            with self.subTest(scope=scope):
                if operation == 'read':
                    client.list_messages(query='in:inbox')
                    request_call = service.users.return_value.messages.return_value.list.call_args
                elif operation == 'modify':
                    client.archive('message')
                    request_call = service.users.return_value.messages.return_value.modify.call_args
                else:
                    client.send_message(to='user@example.com', subject='Hello', body='Body')
                    request_call = service.users.return_value.messages.return_value.send.call_args

                credentials = build.call_args.kwargs['credentials']
                self.assertIsInstance(credentials, OAuthCredentials)
                self.assertEqual(credentials.scopes, [scope])
                self.assertEqual(request_call.kwargs['userId'], 'me')
                build.assert_called_once_with(
                    'gmail',
                    'v1',
                    credentials=credentials,
                    cache_discovery=False,
                )

    @patch('libs.clients.gmail.client.build_google_credentials')
    @patch('googleapiclient.discovery.build')
    def test_default_factory_passes_oauth_scopes_to_shared_builder(
        self,
        build: MagicMock,
        build_credentials: MagicMock,
    ) -> None:
        """Delegate auth selection to the shared builder and disable discovery caching."""
        envelope = '{"chief_google_oauth":1}'
        credentials = MagicMock()
        build_credentials.return_value = credentials

        _build_service(envelope, None)

        build_credentials.assert_called_once_with(
            envelope,
            service_account_scopes=SCOPES,
            subject=None,
            require_service_account_subject=True,
        )
        build.assert_called_once_with(
            'gmail',
            'v1',
            credentials=credentials,
            cache_discovery=False,
        )

    @patch('libs.clients.gmail.client.build_google_credentials')
    @patch('googleapiclient.discovery.build', side_effect=RuntimeError('private-provider-body'))
    def test_factory_failure_clears_credential_service_and_vendor_context(
        self,
        _build: MagicMock,
        build_credentials: MagicMock,
    ) -> None:
        """Release raw credentials and built objects before mapping a vendor failure."""
        envelope = '{"refresh_token":"private-refresh-token"}'
        credentials = MagicMock()
        credentials.private_marker = 'private-client-secret'
        service = _build.return_value
        build_credentials.return_value = credentials

        try:
            _build_service(envelope, None)
        except GmailAuthError as failure:
            frames = _gmail_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is credentials for value in retained_values))
            self.assertFalse(any(value is service for value in retained_values))
            retained = repr(frames)
            for sentinel in (
                envelope,
                'private-refresh-token',
                'private-client-secret',
                'private-provider-body',
            ):
                self.assertNotIn(sentinel, retained)
                self.assertNotIn(sentinel, str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('Gmail service construction failure did not raise GmailAuthError')

    def test_missing_credential_raises_auth_failure(self) -> None:
        client = _client_with_service(MagicMock(), token=None)
        with self.assertRaisesMessage(GmailAuthError, 'no Google credential resolved'):
            client.list_messages(query='in:inbox')

    def test_invalid_json_maps_to_safe_google_auth_failure(self) -> None:
        with self.assertRaisesMessage(
            GmailAuthError,
            'failed to build Gmail credentials',
        ):
            _build_service('not-json', 'me@example.com')

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

    def test_provider_failure_releases_request_service_and_private_body(self) -> None:
        """Map provider detail without retaining operation-local objects in client frames."""
        response = MagicMock()
        response.status = 403
        provider_body = b'provider-body-access-token-secret-sentinel'
        provider_failure = HttpError(response, provider_body)
        request = MagicMock()
        request.execute.side_effect = provider_failure
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value = request
        client = _client_with_service(service)

        try:
            client.get_message('m1')
        except GmailAuthError as failure:
            frames = _gmail_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is request for value in retained_values))
            self.assertFalse(any(value is service for value in retained_values))
            self.assertNotIn(provider_body.decode(), repr(frames))
            self.assertNotIn(provider_body.decode(), str(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail('Provider rejection did not raise GmailAuthError')

    def test_success_closes_service_without_replacing_result_when_close_fails(self) -> None:
        """Return a successful Gmail result even when transport cleanup fails."""
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'm1'}],
        }
        service.close.side_effect = RuntimeError('private-close-detail')
        client = _client_with_service(service)

        result = client.list_messages(query='in:inbox')

        self.assertEqual(result['message_ids'], ['m1'])
        service.close.assert_called_once_with()

    def test_primary_failure_survives_close_failure_without_service_retention(self) -> None:
        """Preserve the mapped request failure while swallowing private close detail."""
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = _http_failure(403)
        service.close.side_effect = RuntimeError('private-close-detail')
        client = _client_with_service(service)

        try:
            client.get_message('m1')
        except GmailAuthError as failure:
            frames = _gmail_traceback_locals(failure)
            retained_values = [value for _name, values in frames for value in values.values()]
            self.assertFalse(any(value is service for value in retained_values))
            self.assertNotIn('private-close-detail', repr(frames))
            self.assertNotIn('private-close-detail', str(failure))
            service.close.assert_called_once_with()
        else:
            self.fail('Gmail request rejection did not raise GmailAuthError')

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
