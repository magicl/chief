# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Gmail source adapter: poll a mailbox by search query into a queue.

Filtering (including U1's `-label:x-*` exclusion) lives entirely in `config.query` — the
adapter has no triage logic. Emits the shared `{data, ref}` payload envelope.
"""

from __future__ import annotations

from typing import Any

from libs.clients.gmail import GmailClient
from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter
from libs.sources.dedup import (
    dedupe_enabled,
    gmail_external_id,
    should_skip_known,
    validate_dedupe_config,
)

_DEFAULT_MAX_RESULTS = 25
_MAX_INLINE_BODY_CHARS = 2000


def _header(message: dict[str, Any], name: str) -> str | None:
    """Return a header value (case-insensitive) from a Gmail message payload."""
    for hdr in message.get('payload', {}).get('headers', []):
        if hdr.get('name', '').lower() == name.lower():
            value = hdr.get('value')
            return value if isinstance(value, str) else None
    return None


def _parse_to_addresses(message: dict[str, Any]) -> list[str]:
    """Split the To header into individual address strings."""
    to_hdr = _header(message, 'To')
    if not to_hdr:
        return []
    return [part.strip() for part in to_hdr.split(',') if part.strip()]


def _walk_payload_parts(part: dict[str, Any], out: list[dict[str, Any]]) -> None:
    """Collect attachment metadata from a message payload tree (metadata format)."""
    filename = part.get('filename')
    body = part.get('body') or {}
    attachment_id = body.get('attachmentId')
    if filename and attachment_id:
        out.append(
            {
                'attachment_id': attachment_id,
                'filename': filename,
                'mime_type': part.get('mimeType'),
                'size': body.get('size'),
            }
        )
    for child in part.get('parts', []) or []:
        if isinstance(child, dict):
            _walk_payload_parts(child, out)


def _attachment_meta(message: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Return whether the message has attachments and lightweight metadata for each."""
    attachments: list[dict[str, Any]] = []
    payload = message.get('payload')
    if isinstance(payload, dict):
        _walk_payload_parts(payload, attachments)
    return bool(attachments), attachments


def _inline_body(message: dict[str, Any], *, include_body: bool) -> str | None:
    """Return a truncated plain-text preview when ``include_body`` is enabled."""
    if not include_body:
        return None
    snippet = message.get('snippet')
    if not isinstance(snippet, str) or not snippet:
        return None
    if len(snippet) <= _MAX_INLINE_BODY_CHARS:
        return snippet
    return snippet[:_MAX_INLINE_BODY_CHARS] + '…'


class GmailSourceAdapter(SourceAdapter):
    adapter_type = 'gmail'
    credential_type = 'gmail'

    def validate_config(self, config: dict[str, Any]) -> None:
        """Require non-empty ``subject`` and ``query``; validate optional fields."""
        subject = config.get('subject')
        if not isinstance(subject, str) or not subject:
            raise ValueError('subject must be a non-empty string')
        query = config.get('query')
        if not isinstance(query, str) or not query:
            raise ValueError('query must be a non-empty string')
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        if not isinstance(max_results, int) or max_results < 1:
            raise ValueError('max_results must be a positive integer')
        include_body = config.get('include_body', False)
        if not isinstance(include_body, bool):
            raise ValueError('include_body must be a boolean')
        validate_dedupe_config(config)

    def poll(
        self,
        *,
        config: dict[str, Any],
        put_item: PutItemCallback,
        credential_supplier: SecretSupplier | None,
        known_external_ids: frozenset[str] | None = None,
    ) -> PollResult:
        """List messages by query and enqueue one ``{data, ref}`` envelope per message."""
        max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
        include_body = config.get('include_body', False)
        dedupe = dedupe_enabled(config)
        client = GmailClient(token_supplier=credential_supplier or (lambda: None), config=config)
        message_ids = client.list_message_ids(query=config['query'], max_results=max_results)
        enqueued = 0
        for message_id in message_ids:
            if should_skip_known(
                dedupe=dedupe,
                external_id=message_id,
                known_external_ids=known_external_ids,
            ):
                continue
            msg = client.get_message(message_id, fmt='metadata')
            has_attachments, attachments = _attachment_meta(msg)
            data: dict[str, Any] = {
                'id': msg.get('id'),
                'thread_id': msg.get('threadId'),
                'from': _header(msg, 'From'),
                'to': _parse_to_addresses(msg),
                'subject': _header(msg, 'Subject'),
                'snippet': msg.get('snippet'),
                'received_at': _header(msg, 'Date'),
                'label_ids': msg.get('labelIds', []),
                'has_attachments': has_attachments,
                'attachments': attachments,
            }
            body_preview = _inline_body(msg, include_body=include_body)
            if body_preview is not None:
                data['body_preview'] = body_preview
            envelope = {
                'data': data,
                'ref': {'service': 'gmail', 'resource_type': 'message', 'resource_id': message_id},
            }
            ext_id = gmail_external_id(
                message_id,
                history_id=msg.get('historyId') if isinstance(msg.get('historyId'), str) else None,
                dedupe=dedupe,
            )
            result = put_item(payload=envelope, external_id=ext_id)
            if result.created:
                enqueued += 1
        return PollResult(items_seen=len(message_ids), items_enqueued=enqueued)
