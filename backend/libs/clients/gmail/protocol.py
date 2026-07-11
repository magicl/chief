# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Protocol for Gmail clients used by the Gmail tool."""

from __future__ import annotations

from typing import Any, Protocol


class GmailClientProtocol(Protocol):
    """Structural interface for the Gmail methods dispatched by GmailTool."""

    def list_messages(self, *, query: str, max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        """Return one page of message ids for a Gmail query."""

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Fetch one message by id."""

    def list_labels(self) -> list[dict[str, Any]]:
        """Return label id/name records for the mailbox."""

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Return a decoded attachment payload for one message."""

    def ensure_label_ids(self, names: tuple[str, ...]) -> list[str]:
        """Resolve label names to ids, creating missing labels if needed."""

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...] = (), remove: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Add and remove label ids on a message."""

    def archive(self, message_id: str) -> dict[str, Any]:
        """Archive one message."""

    def report_spam(self, message_id: str) -> dict[str, Any]:
        """Move one message to spam."""

    def trash(self, message_id: str) -> dict[str, Any]:
        """Move one message to trash."""

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send one plain-text message."""
