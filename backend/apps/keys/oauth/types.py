# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shared records and protocol for registered OAuth providers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

OAuthSupport = Literal['current', 'future']


@dataclass(frozen=True, slots=True)
class OAuthCapability:
    """Describe one allowlisted provider permission exposed to users."""

    id: str
    label: str
    description: str
    scope: str
    support: OAuthSupport


class OAuthProvider(Protocol):
    """Define provider operations while keeping lifecycle services provider-neutral."""

    id: str
    credential_type: str
    capabilities: tuple[OAuthCapability, ...]

    def normalize_capabilities(self, capability_ids: Iterable[str]) -> tuple[str, ...]:
        """Validate capability IDs and return them in stable catalog order."""
        raise NotImplementedError

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Build the provider authorization URL for a validated declaration."""
        raise NotImplementedError

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Exchange a one-use code and serialize only the persistent grant."""
        raise NotImplementedError

    def materialize_runtime(self, *, grant_payload: str, capability_ids: tuple[str, ...]) -> str:
        """Combine a stored grant with deployment secrets for one runtime operation."""
        raise NotImplementedError
