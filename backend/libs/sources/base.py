# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Source adapter protocol (Django-free)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


class PutItemCallback(Protocol):
    def __call__(
        self,
        *,
        payload: dict[str, Any],
        external_id: str,
    ) -> PutItemResult: ...


@dataclass(frozen=True, slots=True)
class PutItemResult:
    item_id: UUID
    created: bool


SecretSupplier = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class PollResult:
    items_seen: int
    items_enqueued: int


class SourceAdapter(ABC):
    adapter_type: str
    credential_type: str | None = None

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> None:
        """Raise ValueError on invalid config."""

    @abstractmethod
    def poll(
        self,
        *,
        config: dict[str, Any],
        put_item: PutItemCallback,
        credential_supplier: SecretSupplier | None,
        known_external_ids: frozenset[str] | None = None,
    ) -> PollResult:
        """Poll upstream items; *known_external_ids* avoids re-fetch when ``dedupe`` is on."""
        raise NotImplementedError
