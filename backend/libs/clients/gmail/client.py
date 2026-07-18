# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic Gmail API v1 client for one impersonated mailbox (Django-free).

Auth is service account + domain-wide delegation: the SA JSON is supplied lazily by
``token_supplier`` and impersonates ``config['subject']``. The client never stores the
plaintext credential or the built service beyond a single method call (secret-retention
rule, see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, cast

from libs.clients.gmail.errors import (
    GmailAPIError,
    GmailAuthError,
    GmailError,
    GmailNotFoundError,
)

SCOPES = ('https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/gmail.send')

ServiceFactory = Callable[[str, str], Any]

# Max decoded attachment bytes returned to callers (10 MiB).
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _map_http_failure(exc: Exception) -> GmailError:
    """Translate a ``googleapiclient.errors.HttpError`` into a typed ``GmailError``."""
    try:
        from googleapiclient.errors import HttpError  # noqa: PLC0415
    except ImportError:
        return GmailAPIError(str(exc))
    if not isinstance(exc, HttpError):
        return GmailAPIError(str(exc))
    status = exc.resp.status if exc.resp is not None else None
    if status in (401, 403):
        return GmailAuthError(str(exc))
    if status == 404:
        return GmailNotFoundError(str(exc))
    return GmailAPIError(str(exc), status=status)


def _build_service(raw_credential: str, subject: str) -> Any:
    """Build a Gmail API service impersonating *subject* from SA JSON (imports google lazily)."""
    from google.oauth2 import (  # noqa: PLC0415 — heavy optional dep, import on use
        service_account,
    )
    from googleapiclient.discovery import build  # noqa: PLC0415

    try:
        info = json.loads(raw_credential)
    except (ValueError, TypeError) as exc:
        raise GmailAuthError('Google service-account credential is not valid JSON') from exc
    try:
        creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=list(SCOPES)
        )
        creds = creds.with_subject(subject)
    except Exception as exc:  # noqa: BLE001 — google raises assorted types on bad keys
        raise GmailAuthError(f'failed to build delegated credentials: {exc}') from exc
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


class GmailClient:
    """Thin wrapper over the Gmail API for a single impersonated mailbox."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        service_factory: ServiceFactory | None = None,
    ) -> None:
        self._token_supplier = token_supplier
        self._config = config or {}
        self._service_factory = service_factory or _build_service

    def _service(self) -> Any:
        """Resolve the credential and build a per-call impersonated service."""
        subject = self._config.get('subject')
        if not subject:
            raise GmailAuthError('config.subject (mailbox to impersonate) is required')
        raw = self._token_supplier()
        if not raw:
            raise GmailAuthError('no Google service-account credential resolved')
        return self._service_factory(raw, subject)

    def _execute(self, request: Any) -> Any:
        """Run one Gmail API request and map vendor ``HttpError`` to ``GmailError``."""
        try:
            return request.execute()
        except Exception as exc:  # noqa: BLE001 — HttpError and transport errors vary
            mapped = _map_http_failure(exc)
            if mapped is not exc:
                raise mapped from exc
            if isinstance(mapped, GmailError):
                raise mapped from exc
            raise GmailAPIError(str(exc)) from exc

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        """Return ``{message_ids, next_page_token}`` for one page of a Gmail search query."""
        resp = self._execute(
            self._service().users().messages().list(userId='me', q=query, maxResults=max_results, pageToken=page_token)
        )
        return {
            'message_ids': [m['id'] for m in resp.get('messages', [])],
            'next_page_token': resp.get('nextPageToken'),
        }

    def list_message_ids(self, *, query: str, max_results: int = 100) -> list[str]:
        """Paginate ``list_messages`` until *max_results* ids are collected or pages end."""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            page_size = min(100, max_results - len(ids))
            listing = self.list_messages(query=query, max_results=page_size, page_token=page_token)
            ids.extend(listing['message_ids'])
            page_token = listing['next_page_token']
            if not page_token or not listing['message_ids']:
                break
        return ids[:max_results]

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Fetch one message (``fmt`` is ``metadata`` or ``full``)."""
        return cast(
            dict[str, Any],
            self._execute(self._service().users().messages().get(userId='me', id=message_id, format=fmt)),
        )

    def list_labels(self) -> list[dict[str, Any]]:
        """Return label id/name records for the mailbox."""
        resp = self._execute(self._service().users().labels().list(userId='me'))
        return list(resp.get('labels', []))

    def create_label(self, name: str) -> dict[str, Any]:
        """Create a user label and return its id/name record."""
        body = {
            'name': name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show',
        }
        return cast(
            dict[str, Any],
            self._execute(self._service().users().labels().create(userId='me', body=body)),
        )

    def ensure_label_ids(self, names: tuple[str, ...]) -> list[str]:
        """Resolve label names to ids, creating any user labels that do not yet exist."""
        if not names:
            return []
        by_name = {lbl['name']: lbl['id'] for lbl in self.list_labels() if lbl.get('name') and lbl.get('id')}
        ids: list[str] = []
        for name in names:
            label_id = by_name.get(name)
            if label_id is None:
                created = self.create_label(name)
                label_id = created['id']
                by_name[name] = label_id
            ids.append(label_id)
        return ids

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Add/remove label ids on a message."""
        body = {'addLabelIds': list(add), 'removeLabelIds': list(remove)}
        return cast(
            dict[str, Any],
            self._execute(self._service().users().messages().modify(userId='me', id=message_id, body=body)),
        )

    def archive(self, message_id: str) -> dict[str, Any]:
        """Remove the INBOX label (archive)."""
        return self.modify_labels(message_id, remove=('INBOX',))

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Move a message to spam."""
        return self.modify_labels(message_id, add=('SPAM',), remove=('INBOX',))

    def trash(self, message_id: str) -> dict[str, Any]:
        """Move a message to trash (denied by default in example configs)."""
        return cast(
            dict[str, Any],
            self._execute(self._service().users().messages().trash(userId='me', id=message_id)),
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Download an attachment; returns decoded bytes plus metadata (size-guarded)."""
        raw = cast(
            dict[str, Any],
            self._execute(
                self._service()
                .users()
                .messages()
                .attachments()
                .get(userId='me', messageId=message_id, id=attachment_id)
            ),
        )
        data_b64 = raw.get('data', '')
        decoded = base64.urlsafe_b64decode(data_b64 + '==='[: (4 - len(data_b64) % 4) % 4])
        if len(decoded) > _MAX_ATTACHMENT_BYTES:
            raise GmailAPIError(
                f'attachment exceeds max size ({_MAX_ATTACHMENT_BYTES} bytes)',
                status=413,
            )
        return {
            'attachment_id': attachment_id,
            'size': raw.get('size', len(decoded)),
            'data': decoded,
            'mime_type': raw.get('mimeType'),
        }

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send a plain-text message (requires the gmail.send scope; denied by default)."""
        email = EmailMessage()
        email['To'] = to
        email['Subject'] = subject
        email.set_content(body)
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
        return cast(
            dict[str, Any],
            self._execute(self._service().users().messages().send(userId='me', body={'raw': raw})),
        )
