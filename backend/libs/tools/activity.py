# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django-free activity recording contract available to tools."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

TERMINAL_COMPLETION_STATUSES = frozenset({'succeeded', 'failed', 'cancelled'})


def validate_completion_status(status: str) -> None:
    """Reject statuses that would leave a completed activity nonterminal."""
    if status not in TERMINAL_COMPLETION_STATUSES:
        raise ValueError('complete status must be terminal')


@dataclass(frozen=True)
class ActivityRef:
    """Opaque lifecycle handle returned by activity recorders."""

    id: UUID
    seq: int
    revision: int
    kind: str
    status: str


class ActivityRecorder(Protocol):
    """Record nested activity lifecycle without exposing persistence details.

    Parent scopes are context-local: nested calls and copied asyncio task
    contexts inherit them. A newly spawned OS thread does not; thread-spawning
    callers must use ``contextvars.copy_context`` or call ``push_parent`` in
    that thread when inheritance is intended.
    """

    def start(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'running',
    ) -> ActivityRef:
        """Create an activity beneath the current parent scope."""

    def complete(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'succeeded',
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Complete an activity with terminal status and optional usage."""

    def fail(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Mark an activity failed while preserving available lifecycle metadata."""

    def status_note(
        self,
        *,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Emit an immutable status note beneath the current parent."""

    def span(
        self,
        *,
        name: str,
        summary: str = '',
    ) -> AbstractContextManager[ActivityRef]:
        """Scope nested work under a span completed or failed on exit."""

    def push_parent(self, activity_id: UUID | None) -> AbstractContextManager[None]:
        """Temporarily override the parent used by nested activity creates."""

    def link_subagent(
        self,
        *,
        agent_id: UUID,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Create a linked child session through a session-aware recorder."""


class NoOpActivityRecorder:
    """Return synthetic handles when a tool legitimately runs offline."""

    def start(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'running',
    ) -> ActivityRef:
        """Return a synthetic start handle without persisting its metadata."""
        del name, summary, details
        return ActivityRef(id=uuid4(), seq=0, revision=1, kind=kind, status=status)

    def complete(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = 'succeeded',
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Return a synthetic terminal handle for the supplied identity."""
        validate_completion_status(status)
        del summary, details, model, input_tokens, output_tokens, cost_usd, latency_ms
        return ActivityRef(id=activity_id, seq=0, revision=1, kind='', status=status)

    def fail(
        self,
        activity_id: UUID,
        *,
        summary: str,
        details: dict[str, Any] | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Return a synthetic failed handle for the supplied identity."""
        del summary, details, model, input_tokens, output_tokens, cost_usd, latency_ms
        return ActivityRef(id=activity_id, seq=0, revision=1, kind='', status='failed')

    def status_note(
        self,
        *,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Return a synthetic terminal status-note handle."""
        return self.start(
            kind='status',
            name=name,
            summary=summary,
            details=details,
            status='succeeded',
        )

    @contextmanager
    def span(self, *, name: str, summary: str = '') -> Iterator[ActivityRef]:
        """Yield a synthetic span while preserving context-manager semantics."""
        yield self.start(kind='span', name=name, summary=summary)

    @contextmanager
    def push_parent(self, activity_id: UUID | None) -> Iterator[None]:
        """Accept an offline parent scope without retaining any state."""
        del activity_id
        yield

    def link_subagent(
        self,
        *,
        agent_id: UUID,
        name: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> ActivityRef:
        """Refuse child-session linkage because no session boundary exists."""
        del agent_id, name, summary, details
        raise RuntimeError('sub-agent linking requires a session recorder')
