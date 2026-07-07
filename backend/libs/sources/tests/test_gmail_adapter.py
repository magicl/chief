# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the Gmail source adapter (client stubbed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

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


class TestGmailSourceAdapter(OTestCase):
    def setUp(self) -> None:
        adapter = get_adapter('gmail')
        if adapter is None:
            raise RuntimeError('gmail adapter not registered')
        self.adapter = adapter

    def test_validate_config_requires_subject_and_query(self) -> None:
        self.adapter.validate_config({'subject': 'me@example.com', 'query': 'in:inbox'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'query': 'in:inbox'})
        with self.assertRaises(ValueError):
            self.adapter.validate_config({'subject': 'me@example.com'})

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
