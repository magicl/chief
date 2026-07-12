# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""In-memory Gmail client for tool and workflow tests."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from libs.clients.gmail.errors import GmailNotFoundError


class MockGmailClient:
    """Small in-memory GmailClient replacement with deterministic mutation records."""

    def __init__(self, *, token_supplier: Callable[[], str | None], config: dict[str, Any] | None = None) -> None:
        """Create a mock with the same constructor shape as the real Gmail client."""
        self._token_supplier = token_supplier
        self._config = config or {}
        self._messages: dict[str, dict[str, Any]] = {}
        self._attachments: dict[str, dict[str, dict[str, Any]]] = {}
        self._labels: dict[str, dict[str, Any]] = {}
        self._next_label_seq = 1
        self._next_sent_seq = 1
        self.labeled: list[dict[str, Any]] = []
        self.archived: list[str] = []
        self.spam: list[str] = []
        self.trashed: list[str] = []
        self.sent_messages: list[dict[str, Any]] = []
        for label_id in ('INBOX', 'SPAM', 'TRASH', 'SENT', 'UNREAD'):
            self.seed_label(label_id, label_id=label_id)

    def seed_label(self, name: str, *, label_id: str | None = None) -> dict[str, Any]:
        """Add or replace a label record and return its stored shape."""
        resolved_id = label_id or self._new_label_id()
        label = {'id': resolved_id, 'name': name}
        self._labels[resolved_id] = label
        return deepcopy(label)

    def seed_message(
        self,
        message_id: str,
        *,
        labels: tuple[str, ...] = (),
        message: dict[str, Any] | None = None,
        attachments: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Add a message and optional attachments to the in-memory mailbox."""
        stored = deepcopy(message or {})
        stored['id'] = message_id
        stored['labelIds'] = list(labels)
        self._messages[message_id] = stored
        self._attachments[message_id] = deepcopy(attachments or {})
        return deepcopy(stored)

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        """Return ids for seeded messages matching the supported subset of Gmail queries."""
        matching_ids = [
            message_id for message_id, message in self._messages.items() if self._matches_query(message, query)
        ]
        start = int(page_token or 0)
        end = start + max_results
        next_page_token = str(end) if end < len(matching_ids) else None
        return {'message_ids': matching_ids[start:end], 'next_page_token': next_page_token}

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Fetch a seeded message by id; *fmt* is accepted for client compatibility."""
        del fmt
        if message_id not in self._messages:
            raise GmailNotFoundError(f'gmail message not found: {message_id}')
        return deepcopy(self._messages[message_id])

    def list_labels(self) -> list[dict[str, Any]]:
        """Return all known labels in insertion order."""
        return deepcopy(list(self._labels.values()))

    def create_label(self, name: str) -> dict[str, Any]:
        """Create a synthetic user label unless a label with the same name exists."""
        existing = self._label_id_for_name(name)
        if existing is not None:
            return deepcopy(self._labels[existing])
        return self.seed_label(name)

    def ensure_label_ids(self, names: tuple[str, ...]) -> list[str]:
        """Resolve label names to ids, creating deterministic synthetic ids for unknown names."""
        return [self.create_label(name)['id'] for name in names]

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Apply label mutations to a message and record the requested add/remove sets."""
        message = self._message_ref(message_id)
        label_ids = [label_id for label_id in message.get('labelIds', []) if label_id not in remove]
        for label_id in add:
            if label_id not in label_ids:
                label_ids.append(label_id)
        message['labelIds'] = label_ids
        self.labeled.append({'message_id': message_id, 'add': list(add), 'remove': list(remove)})
        return deepcopy(message)

    def archive(self, message_id: str) -> dict[str, Any]:
        """Archive a message by removing INBOX and recording the message id."""
        self.archived.append(message_id)
        return self.modify_labels(message_id, remove=('INBOX',))

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Move a message to spam and record the message id."""
        self.spam.append(message_id)
        return self.modify_labels(message_id, add=('SPAM',), remove=('INBOX',))

    def trash(self, message_id: str) -> dict[str, Any]:
        """Move a message to trash and record the message id."""
        self.trashed.append(message_id)
        return self.modify_labels(message_id, add=('TRASH',), remove=('INBOX',))

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Fetch a seeded attachment payload by message and attachment id."""
        if message_id not in self._attachments or attachment_id not in self._attachments[message_id]:
            raise GmailNotFoundError(f'gmail attachment not found: {message_id}/{attachment_id}')
        return deepcopy(self._attachments[message_id][attachment_id])

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        """Record an outgoing plain-text message and return a synthetic sent id."""
        message_id = f'mock-sent-{self._next_sent_seq}'
        self._next_sent_seq += 1
        sent = {'id': message_id, 'to': to, 'subject': subject, 'body': body}
        self.sent_messages.append(deepcopy(sent))
        self.seed_message(message_id, labels=('SENT',), message={'to': to, 'subject': subject, 'body': body})
        return {'id': message_id}

    def _message_ref(self, message_id: str) -> dict[str, Any]:
        """Return the mutable stored message or raise a typed not-found failure."""
        if message_id not in self._messages:
            raise GmailNotFoundError(f'gmail message not found: {message_id}')
        return self._messages[message_id]

    def _matches_query(self, message: dict[str, Any], query: str) -> bool:
        """Match the small query subset needed by tests and local agent workflows."""
        labels = set(message.get('labelIds', []))
        for token in query.lower().split():
            if token == 'in:anywhere':  # nosec B105 - Gmail query literal, not a credential.
                continue
            if token.startswith('in:'):
                label_id = token.removeprefix('in:').upper()
                if label_id not in labels:
                    return False
            elif token.startswith('label:'):
                label_name = token.removeprefix('label:')
                label_id = self._label_id_for_name(label_name) or label_name
                if label_id not in labels:
                    return False
        return True

    def _label_id_for_name(self, name: str) -> str | None:
        """Find a label id by case-insensitive label name."""
        lowered = name.lower()
        for label_id, label in self._labels.items():
            if str(label.get('name', '')).lower() == lowered:
                return label_id
        return None

    def _new_label_id(self) -> str:
        """Allocate the next deterministic synthetic user-label id."""
        label_id = f'Label_{self._next_label_seq}'
        self._next_label_seq += 1
        return label_id
