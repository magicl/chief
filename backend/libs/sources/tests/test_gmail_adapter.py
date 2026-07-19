# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the Gmail source adapter (client stubbed)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from libs.clients.gmail.errors import GmailAPIError
from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter

from olib.py.django.test.cases import OTestCase


class _FakeGmailClient:
    """Stand-in for GmailClient returning canned messages."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        del query, max_results, page_token
        return {'message_ids': ['m1', 'm2'], 'next_page_token': None}

    def list_message_ids(self, *, query: str, max_results: int = 100) -> list[str]:
        del query, max_results
        return ['m1', 'm2']

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        return {
            'id': message_id,
            'threadId': f't-{message_id}',
            'snippet': 'hello',
            'labelIds': ['INBOX'],
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'alice@example.com'},
                    {'name': 'To', 'value': 'me@example.com, bob@example.com'},
                    {'name': 'Subject', 'value': 'Q3'},
                    {'name': 'Date', 'value': 'Mon, 06 Jul 2026 10:00:00 +0000'},
                ],
                'parts': [
                    {
                        'filename': 'q3.pdf',
                        'mimeType': 'application/pdf',
                        'body': {'attachmentId': 'ANGj', 'size': 2048},
                    }
                ],
            },
        }

    @contextmanager
    def poll_message_metadata(
        self,
        *,
        query: str,
        max_results: int,
        skip_message_ids: frozenset[str] = frozenset(),
    ) -> Iterator[tuple[list[str], Iterator[tuple[str, dict[str, Any]]]]]:
        """Yield one lazy source poll batch while honoring known-message skips."""
        message_ids = self.list_message_ids(query=query, max_results=max_results)
        messages = (
            (message_id, self.get_message(message_id, fmt='metadata'))
            for message_id in message_ids
            if message_id not in skip_message_ids
        )
        try:
            yield message_ids, messages
        finally:
            messages.close()


class TestGmailSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('gmail')
        if adapter is None:
            raise RuntimeError('gmail adapter not registered')
        self.adapter = adapter

    def test_validate_config_requires_query_and_allows_omitted_subject(self) -> None:
        """Accept OAuth structure without a subject while keeping query mandatory."""
        self.adapter.validate_config({'subject': 'me@example.com', 'query': 'in:inbox'})
        self.adapter.validate_config({'query': 'in:inbox'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'subject': 'me@example.com'})

    def test_validate_config_rejects_malformed_supplied_subject(self) -> None:
        """Require a supplied delegation subject to be a non-empty string."""
        for subject in (None, '', '  ', 1, True):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                self.adapter.validate_config({'subject': subject, 'query': 'in:inbox'})

    def test_validate_config_normalizes_supplied_subject_once(self) -> None:
        """Strip delegation whitespace at validation before runtime client construction."""
        config = {'subject': ' user@example.com ', 'query': 'in:inbox'}

        self.adapter.validate_config(config)

        self.assertEqual(config['subject'], 'user@example.com')

    def test_uses_shared_google_credential_type(self) -> None:
        self.assertEqual(self.adapter.credential_type, 'google')
        self.assertEqual(self.adapter.adapter_type, 'gmail')

    def test_poll_enqueues_envelope_with_ref(self) -> None:
        seen: list[tuple[dict[str, Any], str]] = []

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.gmail.GmailClient', _FakeGmailClient):
            result = self.adapter.poll(
                config={'subject': 'me@example.com', 'query': 'in:inbox', 'max_results': 10},
                put_item=put_item,
                credential_supplier=lambda: '{"sa": true}',
            )

        self.assertEqual(result.items_seen, 2)
        self.assertEqual(result.items_enqueued, 2)
        payload, external_id = seen[0]
        self.assertEqual(external_id, 'm1')
        self.assertEqual(payload['ref'], {'service': 'gmail', 'resource_type': 'message', 'resource_id': 'm1'})
        self.assertEqual(payload['data']['from'], 'alice@example.com')
        self.assertEqual(payload['data']['subject'], 'Q3')
        self.assertEqual(payload['data']['thread_id'], 't-m1')
        self.assertEqual(payload['data']['to'], ['me@example.com', 'bob@example.com'])
        self.assertTrue(payload['data']['has_attachments'])
        self.assertEqual(payload['data']['attachments'][0]['filename'], 'q3.pdf')

    def test_validate_config_rejects_bad_include_body(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'subject': 'me@example.com', 'query': 'in:inbox', 'include_body': 'yes'})

    def test_poll_skips_known_message_ids_when_dedupe_enabled(self) -> None:
        seen_gets: list[str] = []

        class _TrackingFake(_FakeGmailClient):
            def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
                seen_gets.append(message_id)
                msg = super().get_message(message_id, fmt=fmt)
                msg['historyId'] = 'h1'
                return msg

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            del payload, external_id
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.gmail.GmailClient', _TrackingFake):
            result = self.adapter.poll(
                config={'subject': 'me@example.com', 'query': 'in:inbox', 'max_results': 10},
                put_item=put_item,
                credential_supplier=lambda: '{"sa": true}',
                known_external_ids=frozenset({'m1'}),
            )

        self.assertEqual(result.items_seen, 2)
        self.assertEqual(result.items_enqueued, 1)
        self.assertEqual(seen_gets, ['m2'])

    def test_poll_reuses_one_google_service_closes_once_and_persists_no_access_token(self) -> None:
        """Use one operation-local transport for list and metadata without token output."""
        seen: list[tuple[dict[str, Any], str]] = []
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'm1'}, {'id': 'm2'}],
        }
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = [
            _FakeGmailClient().get_message('m1'),
            _FakeGmailClient().get_message('m2'),
        ]
        service.access_token = 'provider-access-token-secret-sentinel'
        raw_credential = 'runtime-credential-access-token-secret-sentinel'
        config = {
            'subject': ' user@example.com ',
            'query': 'in:inbox',
            'max_results': 10,
        }
        self.adapter.validate_config(config)

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Capture one source result without retaining client/provider objects."""
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.clients.gmail.client._build_service', return_value=service) as factory:
            result = self.adapter.poll(
                config=config,
                put_item=put_item,
                credential_supplier=lambda: raw_credential,
            )

        factory.assert_called_once_with(raw_credential, 'user@example.com')
        service.close.assert_called_once_with()
        self.assertEqual(result.items_seen, 2)
        self.assertEqual(result.items_enqueued, 2)
        retained = repr((seen, result, vars(self.adapter)))
        self.assertNotIn('provider-access-token-secret-sentinel', retained)
        self.assertNotIn('runtime-credential-access-token-secret-sentinel', retained)

    def test_poll_enqueues_fetched_messages_before_later_metadata_failure(self) -> None:
        """Stream earlier messages before a later fetch fails and close the sole service."""
        seen: list[tuple[dict[str, Any], str]] = []
        first_message = _FakeGmailClient().get_message('m1')
        service = MagicMock()
        credentials = MagicMock()
        service.credentials = credentials
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            'messages': [{'id': 'm1'}, {'id': 'm2'}],
        }
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = [
            first_message,
            RuntimeError('private-later-provider-detail'),
        ]
        raw_credential = 'runtime-auth-secret-sentinel'

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Capture each item as soon as its metadata has been fetched."""
            seen.append((payload, external_id))
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.clients.gmail.client._build_service', return_value=service) as factory:
            try:
                self.adapter.poll(
                    config={'subject': 'user@example.com', 'query': 'in:inbox'},
                    put_item=put_item,
                    credential_supplier=lambda: raw_credential,
                )
            except GmailAPIError as failure:
                retained_values: list[object] = []
                traceback = failure.__traceback__
                while traceback is not None:
                    if traceback.tb_frame.f_globals.get('__name__') in {
                        'libs.clients.gmail.client',
                        'libs.sources.adapters.gmail',
                    }:
                        retained_values.extend(traceback.tb_frame.f_locals.values())
                    traceback = traceback.tb_next
                self.assertFalse(any(value is service for value in retained_values))
                self.assertFalse(any(value is credentials for value in retained_values))
                retained = repr(retained_values)
                self.assertNotIn(raw_credential, retained)
                self.assertNotIn('private-later-provider-detail', retained)
            else:
                self.fail('Later Gmail metadata rejection did not propagate')

        self.assertEqual([external_id for _payload, external_id in seen], ['m1'])
        factory.assert_called_once_with(raw_credential, 'user@example.com')
        service.close.assert_called_once_with()
