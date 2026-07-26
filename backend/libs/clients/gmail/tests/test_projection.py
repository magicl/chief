# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for compact helpers and Gmail MIME content projection."""

import base64

from libs.clients.compact import (
    ATTACHMENT_LIMIT,
    BODY_CHAR_LIMIT,
    CLICKUP_COMMENT_CHAR_LIMIT,
    CLICKUP_COMMENT_LIMIT,
    CLICKUP_SUBTASK_LIMIT,
    advisory,
    bound_items,
    truncate_text,
)
from libs.clients.gmail.errors import GmailAPIError
from libs.clients.gmail.projection import (
    decode_message_content,
    project_attachment,
    project_authentication,
    project_headers,
    project_labels,
    project_message_full,
    project_message_summary,
    project_mutation_ack,
)

from olib.py.django.test.cases import OTestCase


def _gmail_data(value: bytes) -> str:
    """Encode bytes in Gmail's unpadded base64url body representation."""
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def _headers(*items: tuple[str, str]) -> list[dict[str, str]]:
    """Build Gmail API header records from ordered name/value pairs."""
    return [{'name': name, 'value': value} for name, value in items]


class TestGmailOperationProjection(OTestCase):
    def test_projects_only_valid_label_identity_fields(self) -> None:
        """Keep label identity and type while dropping provider display settings."""
        raw: object = [
            {
                'id': 'INBOX',
                'name': 'Inbox',
                'type': 'system',
                'color': {'backgroundColor': '#fff'},
                'labelListVisibility': 'labelShow',
                'messagesTotal': 42,
            },
            {'id': 'Label_1', 'name': 'Follow Up'},
        ]

        self.assertEqual(
            project_labels(raw),
            [
                {'id': 'INBOX', 'name': 'Inbox', 'type': 'system'},
                {'id': 'Label_1', 'name': 'Follow Up'},
            ],
        )

    def test_omits_malformed_labels_and_invalid_optional_type(self) -> None:
        """Ignore malformed records and omit a non-string optional label type."""
        raw: object = [
            None,
            'provider-noise',
            {'id': 'missing-name'},
            {'id': 7, 'name': 'bad-id'},
            {'id': 'Label_1', 'name': ['bad-name']},
            {'id': 'Label_2', 'name': 'Valid', 'type': {'private': 'detail'}},
        ]

        self.assertEqual(project_labels(raw), [{'id': 'Label_2', 'name': 'Valid'}])
        self.assertEqual(project_labels({'labels': raw}), [])

    def test_projects_decoded_attachment_bytes_as_standard_base64(self) -> None:
        """Encode decoded bytes with standard base64 and expose no provider data key."""
        projected = project_attachment(
            {
                'attachment_id': 'att-1',
                'size': 2,
                'mime_type': 'application/octet-stream',
                'data': b'\xfb\xff',
                'provider_noise': 'private',
            }
        )

        self.assertEqual(
            projected,
            {
                'attachment_id': 'att-1',
                'size': 2,
                'mime_type': 'application/octet-stream',
                'data_base64': '+/8=',
            },
        )
        self.assertNotIn('data', projected)

    def test_attachment_size_comes_from_decoded_bytes(self) -> None:
        """Ignore mismatched provider size metadata in favor of decoded byte length."""
        projected = project_attachment(
            {
                'attachment_id': 'att-1',
                'size': 999,
                'mime_type': 'application/octet-stream',
                'data': b'\x00\x01',
            }
        )

        self.assertEqual(projected['size'], 2)

    def test_invalid_attachment_result_raises_safe_gmail_failure(self) -> None:
        """Map malformed decoded-byte results to a stable provider-safe failure."""
        with self.assertRaisesMessage(GmailAPIError, 'Invalid Gmail attachment response') as caught:
            project_attachment(
                {
                    'attachment_id': 'att-1',
                    'size': 'private-size-detail',
                    'mime_type': 'text/plain',
                    'data': 'private-provider-data',
                }
            )

        self.assertNotIn('private', str(caught.exception))

    def test_projects_compact_mutation_ack_with_valid_labels(self) -> None:
        """Retain only mutation identity and a wholly valid provider label list."""
        projected = project_mutation_ack(
            {
                'id': 'provider-message',
                'threadId': 'private-thread',
                'historyId': 'private-history',
                'labelIds': ['INBOX', 'Label_1'],
                'payload': {'private': 'provider-data'},
            },
            message_id='caller-message',
        )

        self.assertEqual(
            projected,
            {
                'ok': True,
                'message_id': 'provider-message',
                'label_ids': ['INBOX', 'Label_1'],
            },
        )
        self.assertNotIn('success', projected)

    def test_mutation_ack_uses_caller_id_and_omits_malformed_labels(self) -> None:
        """Use the caller identity for empty responses and reject mixed label values."""
        self.assertEqual(
            project_mutation_ack({}, message_id='caller-message'),
            {'ok': True, 'message_id': 'caller-message'},
        )
        self.assertEqual(
            project_mutation_ack(
                {'id': 7, 'labelIds': ['INBOX', {'private': 'detail'}]},
                message_id='caller-message',
            ),
            {'ok': True, 'message_id': 'caller-message'},
        )

    def test_mutation_ack_normalizes_non_empty_message_and_label_ids(self) -> None:
        """Strip accepted provider identifiers and omit a list containing empty ids."""
        self.assertEqual(
            project_mutation_ack(
                {'id': '  provider-message  ', 'labelIds': [' INBOX ', 'Label_1']},
                message_id='caller-message',
            ),
            {
                'ok': True,
                'message_id': 'provider-message',
                'label_ids': ['INBOX', 'Label_1'],
            },
        )
        self.assertEqual(
            project_mutation_ack(
                {'id': '   ', 'labelIds': ['INBOX', '   ']},
                message_id='  caller-message  ',
            ),
            {'ok': True, 'message_id': 'caller-message'},
        )

    def test_mutation_ack_rejects_empty_provider_and_caller_message_ids(self) -> None:
        """Raise a safe tool-mappable failure when neither message id is usable."""
        with self.assertRaisesMessage(GmailAPIError, 'Invalid Gmail mutation response'):
            project_mutation_ack({'id': '   '}, message_id='  ')


class TestGmailHeaderProjection(OTestCase):
    def test_decodes_rfc2047_subject_and_from_values(self) -> None:
        headers = _headers(
            ('Subject', '=?UTF-8?B?SMOpbGxvIPCfmIA=?='),
            ('From', '=?UTF-8?Q?Jos=C3=A9?= <Jose@Example.COM>'),
            ('Authentication-Results', 'private raw authentication evidence'),
        )

        projected = project_headers(headers)

        self.assertEqual(projected['subject'], 'Héllo 😀')
        self.assertEqual(projected['from'], 'José <Jose@Example.COM>')
        self.assertNotIn('Authentication-Results', projected)
        self.assertNotIn('private raw authentication evidence', repr(projected))


class TestGmailAuthenticationProjection(OTestCase):
    def test_projects_full_authentication_and_alignment(self) -> None:
        headers = _headers(
            ('From', 'Sender <sender@Example.COM>'),
            ('Reply-To', 'Replies <reply@example.com>'),
            ('Return-Path', '<bounce@example.com>'),
            (
                'Authentication-Results',
                'mx.google.com; spf=pass smtp.mailfrom=bounce@example.com; '
                'dkim=pass header.d=example.com; '
                'dmarc=pass (p=REJECT sp=NONE dis=NONE) header.from=example.com; '
                'arc=pass (i=1)',
            ),
        )

        self.assertEqual(
            project_authentication(headers),
            {
                'spf': {'verdict': 'pass', 'domain': 'example.com'},
                'dkim': [{'verdict': 'pass', 'domain': 'example.com'}],
                'dmarc': {
                    'verdict': 'pass',
                    'policy': 'reject',
                    'header_from': 'example.com',
                },
                'arc': {'verdict': 'pass'},
                'alignment': {
                    'from_domain': 'example.com',
                    'reply_to_domain': 'example.com',
                    'return_path_domain': 'example.com',
                    'from_matches_reply_to': True,
                    'from_matches_return_path': True,
                },
            },
        )

    def test_retains_multiple_dkim_results_in_source_order(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; dkim=fail header.d=bad.example; '
                'dkim=temperror header.d=slow.example; dkim=pass header.d=good.example',
            ),
        )

        self.assertEqual(
            project_authentication(headers)['dkim'],
            [
                {'verdict': 'fail', 'domain': 'bad.example'},
                {'verdict': 'temperror', 'domain': 'slow.example'},
                {'verdict': 'pass', 'domain': 'good.example'},
            ],
        )

    def test_does_not_trust_received_spf_without_gmail_authserv_id(self) -> None:
        headers = _headers(
            (
                'Received-SPF',
                'softfail (google.com: domain of transitioning Sender@Mail.Example does not designate) '
                'client-ip=192.0.2.1; envelope-from=<Sender@Mail.Example>;',
            ),
        )

        self.assertEqual(
            project_authentication(headers)['spf'],
            {'verdict': 'unknown', 'domain': None},
        )

    def test_arc_authentication_results_cannot_assert_arc_verdict(self) -> None:
        headers = _headers(
            (
                'ARC-Authentication-Results',
                'i=1; mx.example; arc=neutral; dkim=permerror header.d=Arc.Example',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['arc'], {'verdict': 'unknown'})
        self.assertEqual(authentication['dkim'], [])

    def test_ignores_forged_authserv_results_before_trusted_gmail_results(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'attacker.example; spf=pass smtp.mailfrom=attacker.example; '
                'dkim=pass header.d=attacker.example; '
                'dmarc=pass header.from=attacker.example',
            ),
            (
                'Authentication-Results',
                'mx.google.com; spf=fail smtp.mailfrom=sender@example.com; '
                'dkim=fail header.d=example.com; '
                'dmarc=fail (p=QUARANTINE) header.from=example.com',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'fail', 'domain': 'example.com'})
        self.assertEqual(authentication['dkim'], [{'verdict': 'fail', 'domain': 'example.com'}])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'fail', 'policy': 'quarantine', 'header_from': 'example.com'},
        )

    def test_arc_history_does_not_supply_current_mechanism_results(self) -> None:
        headers = _headers(
            (
                'ARC-Authentication-Results',
                'i=2; relay.example; arc=pass; '
                'spf=pass smtp.mailfrom=attacker.example; '
                'dkim=pass header.d=attacker.example; '
                'dmarc=pass header.from=attacker.example',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )
        self.assertEqual(authentication['arc'], {'verdict': 'unknown'})

    def test_missing_evidence_is_unknown_and_comparisons_are_none(self) -> None:
        self.assertEqual(
            project_authentication([]),
            {
                'spf': {'verdict': 'unknown', 'domain': None},
                'dkim': [],
                'dmarc': {
                    'verdict': 'unknown',
                    'policy': None,
                    'header_from': None,
                },
                'arc': {'verdict': 'unknown'},
                'alignment': {
                    'from_domain': None,
                    'reply_to_domain': None,
                    'return_path_domain': None,
                    'from_matches_reply_to': None,
                    'from_matches_return_path': None,
                },
            },
        )

    def test_malformed_authentication_never_becomes_pass(self) -> None:
        headers = _headers(
            ('From', 'not a mailbox'),
            ('Authentication-Results', 'spf=definitely-pass; dkim=; dmarc=PASSING; arc=trusted'),
            ('Received-SPF', 'garbage pass-ish private data'),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )
        self.assertEqual(authentication['arc'], {'verdict': 'unknown'})
        self.assertNotIn('private data', repr(authentication))

    def test_rejects_extended_verdict_tokens(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; spf=pass-ish smtp.mailfrom=sender@example.com; ' + 'dkim=pass-foo header.d=example.com',
            ),
            ('Received-SPF', 'pass-foo envelope-from=<sender@example.com>'),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [{'verdict': 'unknown', 'domain': 'example.com'}])

    def test_rejects_malformed_mailbox_and_authentication_domains(self) -> None:
        headers = _headers(
            ('From', 'Sender <sender@bad..example>'),
            ('Reply-To', 'Replies <reply@bad..example>'),
            ('Return-Path', '<bounce@bad..example>'),
            (
                'Authentication-Results',
                'mx.google.com; spf=pass smtp.mailfrom=bounce@bad..example; '
                'dkim=pass header.d=bad..example; '
                'dmarc=pass header.from=bad..example',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [{'verdict': 'unknown', 'domain': None}])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )
        self.assertEqual(
            authentication['alignment'],
            {
                'from_domain': None,
                'reply_to_domain': None,
                'return_path_domain': None,
                'from_matches_reply_to': None,
                'from_matches_return_path': None,
            },
        )

    def test_ignores_mechanism_like_text_inside_result_comments(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; '
                'spf=pass (reason mentioned dkim=pass header.d=fake.example) '
                'smtp.mailfrom=sender@example.com; '
                'dkim=fail header.d=real.example',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'pass', 'domain': 'example.com'})
        self.assertEqual(authentication['dkim'], [{'verdict': 'fail', 'domain': 'real.example'}])

    def test_comments_cannot_supply_identity_parameters(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; spf=pass (smtp.mailfrom=attacker.example); '
                'dkim=pass (header.d=attacker.example); '
                'dmarc=pass (header.from=attacker.example)',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [{'verdict': 'unknown', 'domain': None}])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )

    def test_unmatched_authentication_delimiters_invalidate_evidence(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; spf=pass smtp.mailfrom=<sender@example.com; '
                'dkim=pass header.d="example.com; '
                'dmarc=pass (p=reject header.from=example.com',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )

    def test_delimited_identity_values_reject_trailing_garbage(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; spf=pass smtp.mailfrom=<sender@example.com>junk; '
                'dkim=pass header.d=<example.com>junk; '
                'dmarc=pass header.from="example.com"junk',
            ),
        )

        authentication = project_authentication(headers)

        self.assertEqual(authentication['spf'], {'verdict': 'unknown', 'domain': None})
        self.assertEqual(authentication['dkim'], [{'verdict': 'unknown', 'domain': None}])
        self.assertEqual(
            authentication['dmarc'],
            {'verdict': 'unknown', 'policy': None, 'header_from': None},
        )

    def test_unknown_dmarc_policy_is_omitted(self) -> None:
        headers = _headers(
            (
                'Authentication-Results',
                'mx.google.com; dmarc=pass (p=MONITOR dis=TRUSTED) header.from=example.com',
            ),
        )

        self.assertEqual(
            project_authentication(headers)['dmarc'],
            {'verdict': 'pass', 'policy': None, 'header_from': 'example.com'},
        )


class TestCompactHelpers(OTestCase):
    def test_limits_match_projection_contract(self) -> None:
        self.assertEqual(BODY_CHAR_LIMIT, 32_000)
        self.assertEqual(ATTACHMENT_LIMIT, 25)
        self.assertEqual(CLICKUP_COMMENT_LIMIT, 10)
        self.assertEqual(CLICKUP_COMMENT_CHAR_LIMIT, 4_000)
        self.assertEqual(CLICKUP_SUBTASK_LIMIT, 25)

    def test_truncate_text_preserves_text_within_limit(self) -> None:
        self.assertEqual(truncate_text('hello', limit=5), ('hello', None))

    def test_truncate_text_reports_omitted_characters_and_ref(self) -> None:
        ref = {
            'service': 'gmail',
            'resource_type': 'message',
            'resource_id': 'm1',
        }
        self.assertEqual(
            truncate_text('abcdef', limit=4, ref=ref),
            (
                'abcd',
                {
                    'truncated': True,
                    'omitted_chars': 2,
                    'ref': ref,
                },
            ),
        )

    def test_bound_items_uses_inferred_total(self) -> None:
        self.assertEqual(
            bound_items(['a', 'b', 'c'], limit=2),
            (
                ['a', 'b'],
                {
                    'truncated': True,
                    'included': 2,
                    'total': 3,
                    'omitted_count': 1,
                },
            ),
        )

    def test_bound_items_uses_supplied_total(self) -> None:
        self.assertEqual(
            bound_items(['a', 'b', 'c'], limit=2, total=5),
            (
                ['a', 'b'],
                {
                    'truncated': True,
                    'included': 2,
                    'total': 5,
                    'omitted_count': 3,
                },
            ),
        )

    def test_advisory_returns_stable_shape(self) -> None:
        self.assertEqual(
            advisory(code='body_truncated', message='Fetch the full message body.'),
            {
                'code': 'body_truncated',
                'message': 'Fetch the full message body.',
            },
        )


class TestGmailMessageProjection(OTestCase):
    def test_summary_projects_decoded_headers_without_body(self) -> None:
        raw = {
            'id': 'm1',
            'threadId': 't1',
            'labelIds': ['INBOX', 'IMPORTANT'],
            'internalDate': '1704067200000',
            'snippet': 'A compact preview',
            'payload': {
                'mimeType': 'text/plain',
                'headers': _headers(
                    ('From', '=?UTF-8?Q?Jos=C3=A9?= <jose@example.com>'),
                    ('To', '"Doe, Jane" <jane@example.com>, Bob <bob@example.com>'),
                    ('Cc', '=?UTF-8?Q?Ren=C3=A9e?= <renee@example.com>'),
                    ('Reply-To', 'Replies <reply@example.com>'),
                    ('Return-Path', '<bounce@example.com>'),
                    ('Subject', '=?UTF-8?B?SMOpbGxvIPCfmIA=?='),
                    ('Message-ID', '<message@example.com>'),
                    ('Date', 'Mon, 1 Jan 2024 01:00:00 +0100'),
                ),
                'body': {'data': _gmail_data(b'Body must not appear in summary')},
            },
        }

        projected = project_message_summary(raw)

        self.assertEqual(projected['id'], 'm1')
        self.assertEqual(projected['thread_id'], 't1')
        self.assertEqual(projected['label_ids'], ['INBOX', 'IMPORTANT'])
        self.assertEqual(projected['from'], 'José <jose@example.com>')
        self.assertEqual(
            projected['to'],
            ['"Doe, Jane" <jane@example.com>', 'Bob <bob@example.com>'],
        )
        self.assertEqual(projected['cc'], ['Renée <renee@example.com>'])
        self.assertEqual(projected['reply_to'], 'Replies <reply@example.com>')
        self.assertEqual(projected['return_path'], '<bounce@example.com>')
        self.assertEqual(projected['subject'], 'Héllo 😀')
        self.assertEqual(projected['message_id'], '<message@example.com>')
        self.assertEqual(projected['date'], 'Mon, 1 Jan 2024 01:00:00 +0100')
        self.assertEqual(projected['received_at'], '2024-01-01T00:00:00+00:00')
        self.assertEqual(projected['snippet'], 'A compact preview')
        self.assertEqual(projected['advisories'], [])
        self.assertNotIn('body', projected)
        self.assertNotIn('body_truncation', projected)

    def test_summary_and_full_have_exact_locked_key_sets(self) -> None:
        raw = {
            'id': 'm-contract',
            'threadId': 't-contract',
            'labelIds': ['INBOX'],
            'internalDate': '1704067200000',
            'snippet': 'Preview',
            'payload': {
                'mimeType': 'text/plain',
                'headers': _headers(
                    ('From', 'Sender <sender@example.com>'),
                    ('To', 'Recipient <recipient@example.com>'),
                    ('Cc', 'Copy <copy@example.com>'),
                    ('Reply-To', 'Reply <reply@example.com>'),
                    ('Return-Path', '<bounce@example.com>'),
                    ('Subject', 'Contract'),
                    ('Message-ID', '<contract@example.com>'),
                    ('Date', 'Mon, 1 Jan 2024 00:00:00 +0000'),
                ),
                'body': {'data': _gmail_data(b'Contract body')},
            },
        }
        summary_keys = {
            'id',
            'thread_id',
            'label_ids',
            'from',
            'to',
            'cc',
            'reply_to',
            'return_path',
            'subject',
            'message_id',
            'date',
            'received_at',
            'snippet',
            'has_attachments',
            'attachments',
            'attachments_meta',
            'authentication',
            'advisories',
        }

        summary = project_message_summary(raw)
        full = project_message_full(raw)

        self.assertEqual(set(summary), summary_keys)
        self.assertEqual(set(full), summary_keys | {'body'})
        self.assertEqual(summary['advisories'], [])
        self.assertEqual(full['advisories'], [])

    def test_metadata_only_summary_collects_attachments_without_body_advisory(self) -> None:
        raw = {
            'id': 'm-metadata',
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'size': 123},
                    },
                    {
                        'mimeType': 'application/pdf',
                        'filename': 'invoice.pdf',
                        'body': {'attachmentId': 'att-invoice', 'size': 456},
                    },
                ],
            },
        }

        summary = project_message_summary(raw)

        self.assertEqual(summary['advisories'], [])
        self.assertTrue(summary['has_attachments'])
        self.assertEqual(
            summary['attachments'],
            [
                {
                    'attachment_id': 'att-invoice',
                    'filename': 'invoice.pdf',
                    'mime_type': 'application/pdf',
                    'size': 456,
                }
            ],
        )

    def test_full_metadata_only_body_retains_malformed_advisory_and_empty_body(self) -> None:
        raw = {
            'id': 'm-metadata-full',
            'payload': {
                'mimeType': 'text/plain',
                'body': {'size': 123},
            },
        }

        projected = project_message_full(raw)

        self.assertEqual(projected['body'], {'text': '', 'source': 'plain'})
        self.assertEqual(
            projected['advisories'],
            [{'code': 'mime_part', 'message': 'Malformed MIME part was omitted.'}],
        )

    def test_full_empty_body_uses_source_semantics_without_raw_fallback(self) -> None:
        plain = {
            'payload': {
                'mimeType': 'text/plain',
                'body': {'data': _gmail_data(b'')},
            }
        }
        html = {
            'payload': {
                'mimeType': 'text/html',
                'body': {'data': _gmail_data(b'')},
            }
        }
        html_multipart = {
            'payload': {
                'mimeType': 'multipart/alternative',
                'parts': [
                    {
                        'mimeType': 'text/html',
                        'body': {'size': 0},
                    }
                ],
            }
        }

        self.assertEqual(project_message_full(plain)['body'], {'text': '', 'source': 'plain'})
        self.assertEqual(project_message_full(html)['body'], {'text': '', 'source': 'html_to_text'})
        self.assertEqual(
            project_message_full(html_multipart)['body'],
            {'text': '', 'source': 'html_to_text'},
        )
        self.assertEqual(project_message_full(plain)['advisories'], [])
        self.assertEqual(project_message_full(html)['advisories'], [])

    def test_full_bounds_body_and_attachments_with_explicit_counts(self) -> None:
        attachment_parts = [
            {
                'mimeType': 'application/octet-stream',
                'filename': f'file-{index}.bin',
                'body': {
                    'attachmentId': f'att-{index}',
                    'size': index,
                    'data': 'provider-encoded-data',
                },
            }
            for index in range(27)
        ]
        raw = {
            'id': 'm-bounded',
            'payload': {
                'mimeType': 'multipart/mixed',
                'headers': _headers(('Subject', 'Bounded message')),
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'x' * (BODY_CHAR_LIMIT + 7))},
                    },
                    *attachment_parts,
                ],
            },
        }

        projected = project_message_full(raw)

        self.assertEqual(projected['body'], {'text': 'x' * BODY_CHAR_LIMIT, 'source': 'plain'})
        self.assertEqual(
            projected['body_truncation'],
            {
                'truncated': True,
                'omitted_chars': 7,
                'ref': {
                    'service': 'gmail',
                    'resource_type': 'message',
                    'resource_id': 'm-bounded',
                },
            },
        )
        self.assertTrue(projected['has_attachments'])
        self.assertEqual(len(projected['attachments']), ATTACHMENT_LIMIT)
        self.assertEqual(
            projected['attachments_meta'],
            {
                'truncated': True,
                'included': 25,
                'total': 27,
                'omitted_count': 2,
            },
        )
        self.assertEqual(
            projected['attachments'][0],
            {
                'attachment_id': 'att-0',
                'filename': 'file-0.bin',
                'mime_type': 'application/octet-stream',
                'size': 0,
            },
        )

    def test_unfetchable_attachment_parts_never_become_body_text(self) -> None:
        raw = {
            'id': 'm-attachment-safety',
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Real message body')},
                    },
                    {
                        'mimeType': 'text/plain',
                        'filename': 'named-secret.txt',
                        'body': {'data': _gmail_data(b'Named attachment secret')},
                    },
                    {
                        'mimeType': 'text/html',
                        'headers': _headers(('Content-Disposition', 'attachment')),
                        'body': {'data': _gmail_data(b'<p>Disposition attachment secret</p>')},
                    },
                    {
                        'mimeType': 'application/pdf',
                        'filename': 'fetchable.pdf',
                        'body': {'attachmentId': 'att-fetchable', 'size': 42},
                    },
                ],
            },
        }
        omitted = {
            'code': 'mime_part',
            'message': 'Attachment without a stable attachment id was omitted.',
        }

        summary = project_message_summary(raw)
        full = project_message_full(raw)

        self.assertEqual(full['body'], {'text': 'Real message body', 'source': 'plain'})
        self.assertNotIn('attachment secret', full['body']['text'].lower())
        self.assertEqual(summary['attachments'], full['attachments'])
        self.assertEqual(
            full['attachments'],
            [
                {
                    'attachment_id': 'att-fetchable',
                    'filename': 'fetchable.pdf',
                    'mime_type': 'application/pdf',
                    'size': 42,
                }
            ],
        )
        self.assertTrue(summary['has_attachments'])
        self.assertTrue(full['has_attachments'])
        self.assertEqual(summary['advisories'], [omitted, omitted])
        self.assertEqual(full['advisories'], [omitted, omitted])

    def test_unfetchable_attachments_do_not_set_has_attachments(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'filename': 'unfetchable.txt',
                'body': {'data': _gmail_data(b'Attachment-only content')},
            }
        }

        summary = project_message_summary(raw)
        full = project_message_full(raw)

        self.assertFalse(summary['has_attachments'])
        self.assertFalse(full['has_attachments'])
        self.assertEqual(summary['attachments'], [])
        self.assertEqual(full['attachments'], [])
        self.assertEqual(full['body'], {'text': '', 'source': 'plain'})

    def test_summary_and_full_share_names_and_nontruncated_attachment_meta(self) -> None:
        raw = {
            'id': 'm-shared',
            'payload': {
                'mimeType': 'multipart/mixed',
                'headers': _headers(('Subject', 'Shared')),
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'short body')},
                    },
                    {
                        'mimeType': 'text/csv',
                        'filename': 'report.csv',
                        'body': {'attachmentId': 'att-report', 'size': 12},
                    },
                ],
            },
        }

        summary = project_message_summary(raw)
        full = project_message_full(raw)

        self.assertEqual(set(summary), set(full) - {'body'})
        self.assertNotIn('body_truncation', full)
        self.assertEqual(
            summary['attachments_meta'],
            {
                'truncated': False,
                'included': 1,
                'total': 1,
                'omitted_count': 0,
            },
        )
        self.assertEqual(summary['attachments'], full['attachments'])

    def test_received_at_falls_back_to_decoded_date_for_invalid_provider_values(self) -> None:
        date = '=?UTF-8?Q?Mon=2C_1_Jan_2024_00=3A00=3A00_+0000?='
        base = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': _headers(('Date', date)),
                'body': {'data': _gmail_data(b'body')},
            }
        }

        internal_dates: tuple[object, ...] = ('not-milliseconds', -1, True, [], 10**30)
        for internal_date in internal_dates:
            with self.subTest(internal_date=internal_date):
                projected = project_message_summary({**base, 'internalDate': internal_date})
                self.assertEqual(projected['date'], 'Mon, 1 Jan 2024 00:00:00 +0000')
                self.assertEqual(projected['received_at'], projected['date'])

    def test_received_at_preserves_exact_milliseconds_and_datetime_boundary(self) -> None:
        cases = (
            ('1704067200123', '2024-01-01T00:00:00.123000+00:00'),
            ('253402300799999', '9999-12-31T23:59:59.999000+00:00'),
        )

        for internal_date, expected in cases:
            with self.subTest(internal_date=internal_date):
                projected = project_message_summary(
                    {
                        'internalDate': internal_date,
                        'payload': {'mimeType': 'text/plain', 'body': {'size': 0}},
                    }
                )
                self.assertEqual(projected['received_at'], expected)

    def test_missing_and_invalid_optional_fields_do_not_leak_raw_values(self) -> None:
        raw = {
            'id': 123,
            'threadId': ['private-thread'],
            'labelIds': ['INBOX', 7, True],
            'snippet': {'raw': 'private-snippet'},
            'historyId': 'private-history',
            'sizeEstimate': 999,
            'raw': 'private-raw-message',
            'payload': {
                'mimeType': 'multipart/mixed',
                'headers': 'private-headers',
                'parts': [],
            },
        }

        projected = project_message_summary(raw)

        self.assertNotIn('id', projected)
        self.assertNotIn('thread_id', projected)
        self.assertEqual(projected['label_ids'], ['INBOX'])
        self.assertNotIn('snippet', projected)
        self.assertFalse(projected['has_attachments'])
        self.assertEqual(projected['attachments'], [])
        self.assertEqual(
            projected['attachments_meta'],
            {
                'truncated': False,
                'included': 0,
                'total': 0,
                'omitted_count': 0,
            },
        )

    def test_projection_recursively_excludes_provider_and_authentication_evidence(self) -> None:
        raw_auth = 'mx.google.com; spf=pass smtp.mailfrom=sender@example.com'
        raw = {
            'id': 'm-safe',
            'unknownTop': {'private': True},
            'historyId': 'private-history',
            'sizeEstimate': 999,
            'raw': 'private-raw-message',
            'payload': {
                'mimeType': 'multipart/mixed',
                'unknownPayload': {'data': 'private-payload'},
                'headers': _headers(
                    ('From', 'Sender <sender@example.com>'),
                    ('Authentication-Results', raw_auth),
                ),
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'unknownPart': {'provider': 'private-part'},
                        'body': {
                            'data': _gmail_data(b'x' * (BODY_CHAR_LIMIT + 1)),
                            'unknownBody': 'private-body',
                        },
                    },
                    {
                        'mimeType': 'text/plain',
                        'body': {
                            'data': '%%%provider-encoded-data%%%',
                            'unknownMalformed': 'private-malformed',
                        },
                    },
                    {
                        'mimeType': 'application/pdf',
                        'filename': 'safe.pdf',
                        'unknownAttachment': 'private-attachment',
                        'body': {
                            'attachmentId': 'att-safe',
                            'size': 5,
                            'data': 'private-attachment-data',
                        },
                    },
                ],
            },
        }

        projected = project_message_full(raw)

        expected_keys: dict[tuple[str, ...], set[str]] = {
            (): {
                'id',
                'from',
                'has_attachments',
                'attachments',
                'attachments_meta',
                'authentication',
                'advisories',
                'body',
                'body_truncation',
            },
            ('body',): {'text', 'source'},
            ('body_truncation',): {'truncated', 'omitted_chars', 'ref'},
            ('body_truncation', 'ref'): {'service', 'resource_type', 'resource_id'},
            ('attachments', '*'): {'attachment_id', 'filename', 'mime_type', 'size'},
            ('attachments_meta',): {'truncated', 'included', 'total', 'omitted_count'},
            ('authentication',): {'spf', 'dkim', 'dmarc', 'arc', 'alignment'},
            ('authentication', 'spf'): {'verdict', 'domain'},
            ('authentication', 'dkim', '*'): {'verdict', 'domain'},
            ('authentication', 'dmarc'): {'verdict', 'policy', 'header_from'},
            ('authentication', 'arc'): {'verdict'},
            ('authentication', 'alignment'): {
                'from_domain',
                'reply_to_domain',
                'return_path_domain',
                'from_matches_reply_to',
                'from_matches_return_path',
            },
            ('advisories', '*'): {'code', 'message'},
        }

        def assert_allowed(value: object, path: tuple[str, ...] = ()) -> None:
            """Require each projected record to match its path-specific locked keys."""
            if isinstance(value, dict):
                self.assertEqual(set(value), expected_keys[path], msg=f'key mismatch at {path}')
                for key, nested in value.items():
                    nested_path = (*path, key)
                    if isinstance(nested, (dict, list)):
                        assert_allowed(nested, nested_path)
            elif isinstance(value, list):
                for nested in value:
                    if isinstance(nested, (dict, list)):
                        assert_allowed(nested, (*path, '*'))

        assert_allowed(projected)
        self.assertNotIn(raw_auth, repr(projected))
        self.assertNotIn('provider-encoded-data', repr(projected))
        self.assertNotIn('private-', repr(projected))
        self.assertEqual(
            projected['advisories'],
            [{'code': 'mime_part', 'message': 'Malformed MIME part was omitted.'}],
        )


class TestGmailContentProjection(OTestCase):
    def test_decodes_plain_text(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'body': {'data': _gmail_data(b'Hello from Gmail')},
            }
        }

        self.assertEqual(
            decode_message_content(raw),
            (
                {'text': 'Hello from Gmail', 'source': 'plain'},
                [],
                [],
            ),
        )

    def test_converts_html_only_to_readable_text(self) -> None:
        html = (
            b'<html><style>.hidden { color: red }</style><body>'
            b'<h1>Hello</h1><p>Visit <a href="https://private.example">the portal</a>.</p>'
            b'<script>sendSecret()</script><img src="https://image.example/x">'
            b'</body></html>'
        )
        raw = {
            'payload': {
                'mimeType': 'text/html',
                'body': {'data': _gmail_data(html)},
            }
        }

        body, attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Hello\nVisit the portal.', 'source': 'html_to_text'})
        self.assertEqual(attachments, [])
        self.assertEqual(advisories, [])

    def test_multipart_alternative_prefers_plain_text(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/alternative',
                'parts': [
                    {
                        'mimeType': 'text/html',
                        'body': {'data': _gmail_data(b'<p>HTML version</p>')},
                    },
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Plain version')},
                    },
                ],
            }
        }

        body, _attachments, _advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Plain version', 'source': 'plain'})

    def test_walks_nested_multipart_and_preserves_independent_sections(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'multipart/related',
                        'parts': [
                            {
                                'mimeType': 'text/html',
                                'body': {'data': _gmail_data(b'<p>Nested HTML</p>')},
                            }
                        ],
                    },
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Outer plain')},
                    },
                ],
            }
        }

        body, _attachments, _advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Nested HTML\nOuter plain', 'source': 'html_to_text'})

    def test_ignores_retained_quoted_printable_header_for_final_bytes(self) -> None:
        final_bytes = 'Olá, café =3D literal'.encode('iso-8859-1')
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Content-Type', 'value': 'text/plain; charset=iso-8859-1'},
                    {'name': 'Content-Transfer-Encoding', 'value': 'quoted-printable'},
                ],
                'body': {'data': _gmail_data(final_bytes)},
            }
        }

        body, _attachments, _advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Olá, café =3D literal', 'source': 'plain'})

    def test_ignores_retained_base64_header_for_final_bytes(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Content-Transfer-Encoding', 'value': 'base64'},
                ],
                'body': {'data': _gmail_data(b'MIMEbase64text')},
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'MIMEbase64text', 'source': 'plain'})
        self.assertEqual(advisories, [])

    def test_passes_through_7bit_content_transfer_encoding(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Content-Transfer-Encoding', 'value': '7bit'},
                ],
                'body': {'data': _gmail_data(b'Seven bit text')},
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Seven bit text', 'source': 'plain'})
        self.assertEqual(advisories, [])

    def test_replaces_bytes_invalid_for_declared_charset(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Content-Type', 'value': 'text/plain; charset=utf-8'},
                ],
                'body': {'data': _gmail_data(b'Invalid byte: \xff')},
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Invalid byte: \ufffd', 'source': 'plain'})
        self.assertEqual(advisories, [])

    def test_unknown_charset_uses_utf8_replacement_fallback(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'text/plain',
                'headers': [
                    {'name': 'Content-Type', 'value': 'text/plain; charset=x-unknown-charset'},
                ],
                'body': {'data': _gmail_data(b'Fallback byte: \xff')},
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Fallback byte: \ufffd', 'source': 'plain'})
        self.assertEqual(advisories, [])

    def test_mixed_tree_combines_alternative_choice_with_independent_html(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'multipart/alternative',
                        'parts': [
                            {
                                'mimeType': 'text/html',
                                'body': {'data': _gmail_data(b'<p>Alternative HTML</p>')},
                            },
                            {
                                'mimeType': 'text/plain',
                                'body': {'data': _gmail_data(b'Alternative plain')},
                            },
                        ],
                    },
                    {
                        'mimeType': 'text/html',
                        'body': {'data': _gmail_data(b'<p>Independent HTML</p>')},
                    },
                ],
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(
            body,
            {
                'text': 'Alternative plain\nIndependent HTML',
                'source': 'html_to_text',
            },
        )
        self.assertEqual(advisories, [])

    def test_multipart_alternative_uses_html_when_plain_is_empty(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/alternative',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'')},
                    },
                    {
                        'mimeType': 'text/html',
                        'body': {'data': _gmail_data(b'<p>HTML fallback</p>')},
                    },
                ],
            }
        }

        body, _attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'HTML fallback', 'source': 'html_to_text'})
        self.assertEqual(advisories, [])

    def test_nesting_limit_preserves_accessible_sibling(self) -> None:
        nested: dict[str, object] = {
            'mimeType': 'text/plain',
            'body': {'data': _gmail_data(b'Too deep')},
        }
        for _index in range(75):
            nested = {'mimeType': 'multipart/mixed', 'parts': [nested]}
        raw = {
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Accessible sibling')},
                    },
                    nested,
                ],
            }
        }

        body, attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Accessible sibling', 'source': 'plain'})
        self.assertEqual(attachments, [])
        self.assertEqual(
            advisories,
            [{'code': 'mime_part', 'message': 'MIME nesting limit reached; deeper parts were omitted.'}],
        )

    def test_lists_attachment_without_using_its_data_as_body(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Message text')},
                    },
                    {
                        'mimeType': 'text/plain',
                        'filename': 'notes.txt',
                        'body': {
                            'attachmentId': 'att-1',
                            'size': 22,
                            'data': _gmail_data(b'attachment body text'),
                        },
                    },
                ],
            }
        }

        self.assertEqual(
            decode_message_content(raw),
            (
                {'text': 'Message text', 'source': 'plain'},
                [
                    {
                        'attachment_id': 'att-1',
                        'filename': 'notes.txt',
                        'mime_type': 'text/plain',
                        'size': 22,
                    }
                ],
                [],
            ),
        )

    def test_malformed_part_adds_safe_advisory_and_preserves_sibling(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'multipart/mixed',
                'parts': [
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': '%%%private-provider-detail%%%'},
                    },
                    {
                        'mimeType': 'text/plain',
                        'body': {'data': _gmail_data(b'Readable sibling')},
                    },
                ],
            }
        }

        body, attachments, advisories = decode_message_content(raw)

        self.assertEqual(body, {'text': 'Readable sibling', 'source': 'plain'})
        self.assertEqual(attachments, [])
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]['code'], 'mime_part')
        self.assertNotIn('private-provider-detail', advisories[0]['message'])

    def test_unsupported_inline_part_adds_advisory(self) -> None:
        raw = {
            'payload': {
                'mimeType': 'application/json',
                'body': {'data': _gmail_data(b'{"secret": true}')},
            }
        }

        body, attachments, advisories = decode_message_content(raw)

        self.assertIsNone(body)
        self.assertEqual(attachments, [])
        self.assertEqual(advisories, [{'code': 'mime_part', 'message': 'Unsupported MIME part was omitted.'}])
