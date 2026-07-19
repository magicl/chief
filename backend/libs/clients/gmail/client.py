# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic Gmail API v1 client for one impersonated mailbox (Django-free).

The Google credential is supplied lazily by ``token_supplier``. Service accounts use
domain-wide delegation through ``config['subject']``; Chief OAuth addresses its authorized
user as ``me`` without delegation. The client never stores plaintext credentials or built
services beyond one method call (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Any, cast

from libs.clients.gmail.errors import (
    GmailAPIError,
    GmailAuthError,
    GmailError,
    GmailNotFoundError,
)
from libs.clients.google_auth import build_google_credentials

SCOPES = ('https://www.googleapis.com/auth/gmail.modify', 'https://www.googleapis.com/auth/gmail.send')

ServiceFactory = Callable[[str, str | None], Any]

# Max decoded attachment bytes returned to callers (10 MiB).
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _map_http_failure(exc: Exception) -> GmailError:
    """Translate a provider failure without formatting private response content."""
    try:
        from googleapiclient.errors import HttpError  # noqa: PLC0415
    except ImportError:
        return GmailAPIError('Gmail request failed')
    if not isinstance(exc, HttpError):
        return GmailAPIError('Gmail request failed')
    status = exc.resp.status if exc.resp is not None else None
    if status in (401, 403):
        return GmailAuthError(f'Gmail request failed (status {status})')
    if status == 404:
        return GmailNotFoundError('Gmail request failed (status 404)')
    return GmailAPIError('Gmail request failed', status=status)


def _build_service(raw_credential: str, subject: str | None) -> Any:
    """Build one Gmail service from operation-local Google credentials."""
    from googleapiclient.discovery import build  # noqa: PLC0415

    credentials: Any = None
    service: Any = None
    build_failed = False
    try:
        try:
            credentials = build_google_credentials(
                raw_credential,
                service_account_scopes=SCOPES,
                subject=subject,
                require_service_account_subject=True,
            )
            service = build(
                'gmail',
                'v1',
                credentials=credentials,
                cache_discovery=False,
            )
            return service
        except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            build_failed = True
    finally:
        # Tracebacks retain arguments and locals, so release every auth-bearing object.
        raw_credential = ''
        subject = None
        credentials = None
        service = None
    if build_failed:
        raise GmailAuthError('failed to build Gmail credentials') from None
    raise GmailAuthError('failed to build Gmail credentials') from None


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
        self._config = dict(config or {})
        subject = self._config.get('subject')
        if isinstance(subject, str):
            self._config['subject'] = subject.strip()
        self._service_factory = service_factory or _build_service

    def _service(self) -> Any:
        """Resolve a Google credential and build one operation-local service."""
        raw_credential: str | None = None
        subject: Any = None
        service: Any = None
        factory_failed = False
        try:
            subject = self._config.get('subject')
            raw_credential = self._token_supplier()
            if not raw_credential:
                raise GmailAuthError('no Google credential resolved')
            try:
                service = self._service_factory(raw_credential, subject)
                return service
            except GmailError:
                raise
            except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                factory_failed = True
        finally:
            raw_credential = None
            subject = None
            service = None
        if factory_failed:
            raise GmailAuthError('failed to build Gmail credentials') from None
        raise GmailAuthError('failed to build Gmail credentials') from None

    def _execute(self, request: Any) -> Any:
        """Run one Gmail API request and map vendor ``HttpError`` to ``GmailError``."""
        mapped: GmailError | None = None
        try:
            try:
                return request.execute()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                mapped = _map_http_failure(exc)
        finally:
            request = None
        if mapped is not None:
            raise mapped from None
        raise GmailAPIError('Gmail request failed') from None

    @staticmethod
    def _close_service(service: Any) -> None:
        """Close a Google transport without replacing the primary operation outcome."""
        close: Any = None
        try:
            close = getattr(service, 'close', None)
            if callable(close):
                close()
        except Exception:  # nosec B110  # pylint: disable=broad-exception-caught  # noqa: BLE001
            pass
        finally:
            service = None
            close = None

    def _list_messages_with_service(
        self,
        service: Any,
        *,
        query: str,
        max_results: int,
        page_token: str | None,
    ) -> dict[str, Any]:
        """Return one Gmail listing page through an already-owned service."""
        response: Any = None
        try:
            response = self._execute(
                service.users()
                .messages()
                .list(
                    userId='me',
                    q=query,
                    maxResults=max_results,
                    pageToken=page_token,
                )
            )
            return {
                'message_ids': [message['id'] for message in response.get('messages', [])],
                'next_page_token': response.get('nextPageToken'),
            }
        finally:
            service = None
            response = None

    def _list_message_ids_with_service(self, service: Any, *, query: str, max_results: int) -> list[str]:
        """Paginate message IDs through one operation-local Gmail service."""
        ids: list[str] = []
        page_token: str | None = None
        try:
            while len(ids) < max_results:
                page_size = min(100, max_results - len(ids))
                listing = self._list_messages_with_service(
                    service,
                    query=query,
                    max_results=page_size,
                    page_token=page_token,
                )
                ids.extend(listing['message_ids'])
                page_token = listing['next_page_token']
                if not page_token or not listing['message_ids']:
                    break
            return ids[:max_results]
        finally:
            service = None

    def _get_message_with_service(self, service: Any, message_id: str, *, fmt: str) -> dict[str, Any]:
        """Fetch one message through an already-owned Gmail service."""
        try:
            return cast(
                dict[str, Any],
                self._execute(service.users().messages().get(userId='me', id=message_id, format=fmt)),
            )
        finally:
            service = None

    def _iter_message_metadata_with_service(
        self,
        service: Any,
        message_ids: list[str],
        skip_message_ids: frozenset[str],
    ) -> Generator[tuple[str, dict[str, Any]]]:
        """Yield metadata incrementally while releasing the shared service reference."""
        message_id: str | None = None
        try:
            for message_id in message_ids:
                if message_id not in skip_message_ids:
                    yield message_id, self._get_message_with_service(service, message_id, fmt='metadata')
        finally:
            service = None
            message_ids = []
            skip_message_ids = frozenset()
            message_id = None

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        """Return ``{message_ids, next_page_token}`` for one page of a Gmail search query."""
        service: Any = None
        try:
            service = self._service()
            return self._list_messages_with_service(
                service,
                query=query,
                max_results=max_results,
                page_token=page_token,
            )
        finally:
            self._close_service(service)
            service = None

    def list_message_ids(self, *, query: str, max_results: int = 100) -> list[str]:
        """Paginate ``list_messages`` until *max_results* ids are collected or pages end."""
        service: Any = None
        try:
            service = self._service()
            return self._list_message_ids_with_service(service, query=query, max_results=max_results)
        finally:
            self._close_service(service)
            service = None

    @contextmanager
    def poll_message_metadata(
        self,
        *,
        query: str,
        max_results: int,
        skip_message_ids: frozenset[str] = frozenset(),
    ) -> Iterator[tuple[list[str], Iterator[tuple[str, dict[str, Any]]]]]:
        """Own one poll service while yielding message metadata incrementally."""
        service: Any = None
        message_ids: list[str] | None = None
        messages: Generator[tuple[str, dict[str, Any]]] | None = None
        try:
            service = self._service()
            message_ids = self._list_message_ids_with_service(service, query=query, max_results=max_results)
            messages = self._iter_message_metadata_with_service(service, message_ids, skip_message_ids)
            yield message_ids, messages
        finally:
            if messages is not None:
                messages.close()
            messages = None
            self._close_service(service)
            service = None
            message_ids = None
            skip_message_ids = frozenset()

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Fetch one message (``fmt`` is ``metadata`` or ``full``)."""
        service: Any = None
        try:
            service = self._service()
            return self._get_message_with_service(service, message_id, fmt=fmt)
        finally:
            self._close_service(service)
            service = None

    def list_labels(self) -> list[dict[str, Any]]:
        """Return label id/name records for the mailbox."""
        service: Any = None
        response: Any = None
        try:
            service = self._service()
            response = self._execute(service.users().labels().list(userId='me'))
            return list(response.get('labels', []))
        finally:
            self._close_service(service)
            service = None
            response = None

    def create_label(self, name: str) -> dict[str, Any]:
        """Create a user label and return its id/name record."""
        body = {
            'name': name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show',
        }
        service: Any = None
        try:
            service = self._service()
            return cast(
                dict[str, Any],
                self._execute(service.users().labels().create(userId='me', body=body)),
            )
        finally:
            self._close_service(service)
            service = None

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
        service: Any = None
        try:
            service = self._service()
            return cast(
                dict[str, Any],
                self._execute(service.users().messages().modify(userId='me', id=message_id, body=body)),
            )
        finally:
            self._close_service(service)
            service = None

    def archive(self, message_id: str) -> dict[str, Any]:
        """Remove the INBOX label (archive)."""
        return self.modify_labels(message_id, remove=('INBOX',))

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Move a message to spam."""
        return self.modify_labels(message_id, add=('SPAM',), remove=('INBOX',))

    def trash(self, message_id: str) -> dict[str, Any]:
        """Move a message to trash (denied by default in example configs)."""
        service: Any = None
        try:
            service = self._service()
            return cast(
                dict[str, Any],
                self._execute(service.users().messages().trash(userId='me', id=message_id)),
            )
        finally:
            self._close_service(service)
            service = None

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Download an attachment; returns decoded bytes plus metadata (size-guarded)."""
        service: Any = None
        raw: dict[str, Any] | None = None
        try:
            service = self._service()
            raw = cast(
                dict[str, Any],
                self._execute(
                    service.users().messages().attachments().get(userId='me', messageId=message_id, id=attachment_id)
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
        finally:
            self._close_service(service)
            service = None
            raw = None

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send a plain-text message (requires the gmail.send scope; denied by default)."""
        email = EmailMessage()
        email['To'] = to
        email['Subject'] = subject
        email.set_content(body)
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
        service: Any = None
        try:
            service = self._service()
            return cast(
                dict[str, Any],
                self._execute(service.users().messages().send(userId='me', body={'raw': raw})),
            )
        finally:
            self._close_service(service)
            service = None
