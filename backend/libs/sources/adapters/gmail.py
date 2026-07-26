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
from libs.clients.gmail.projection import project_message_summary
from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter
from libs.sources.dedup import (
    dedupe_enabled,
    gmail_external_id,
    validate_dedupe_config,
)

_DEFAULT_MAX_RESULTS = 25
_MAX_INLINE_BODY_CHARS = 2000


class GmailSourceAdapter(SourceAdapter):
    adapter_type = 'gmail'
    credential_type = 'google'

    def validate_config(self, config: dict[str, Any]) -> None:
        """Require a query and validate an optional delegation subject."""
        if 'subject' in config:
            subject = config['subject']
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError('subject must be a non-empty string')
            config['subject'] = subject.strip()
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
        client: GmailClient | None = None
        message_ids: list[str] | None = None
        messages: Any = None
        msg: dict[str, Any] | None = None
        try:
            max_results = config.get('max_results', _DEFAULT_MAX_RESULTS)
            include_body = config.get('include_body', False)
            dedupe = dedupe_enabled(config)
            client = GmailClient(token_supplier=credential_supplier or (lambda: None), config=config)
            skip_message_ids = (known_external_ids or frozenset()) if dedupe else frozenset()
            with client.poll_message_metadata(
                query=config['query'],
                max_results=max_results,
                skip_message_ids=skip_message_ids,
            ) as batch:
                message_ids, messages = batch
                enqueued = 0
                for message_id, msg in messages:
                    data: dict[str, Any] = dict(project_message_summary(msg))
                    snippet = data.get('snippet')
                    if include_body and isinstance(snippet, str) and snippet:
                        data['body_preview'] = (
                            snippet
                            if len(snippet) <= _MAX_INLINE_BODY_CHARS
                            else snippet[:_MAX_INLINE_BODY_CHARS] + '…'
                        )
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
        finally:
            credential_supplier = None
            client = None
            message_ids = None
            messages = None
            msg = None
