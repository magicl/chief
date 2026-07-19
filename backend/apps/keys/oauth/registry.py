# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Duplicate-safe registry for OAuth provider implementations."""

from __future__ import annotations

from apps.keys.exceptions import KeyValidationError
from apps.keys.oauth.providers.google import GOOGLE_OAUTH_PROVIDER
from apps.keys.oauth.types import OAuthProvider


class OAuthProviderRegistry:
    """Store provider singletons behind a small provider-neutral lookup boundary."""

    def __init__(self) -> None:
        """Initialize an empty private provider map."""
        self._providers: dict[str, OAuthProvider] = {}

    def register(self, provider: OAuthProvider) -> None:
        """Register one provider, rejecting duplicate IDs without exposing input."""
        if provider.id in self._providers:
            raise KeyValidationError('OAuth provider is already registered')
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> OAuthProvider:
        """Return a provider or raise a fixed safe validation failure."""
        try:
            return self._providers[provider_id]
        except KeyError:
            raise KeyValidationError('Unknown OAuth provider') from None

    def provider_ids(self) -> tuple[str, ...]:
        """Return registered IDs in deterministic registration order."""
        return tuple(self._providers)


OAUTH_PROVIDERS = OAuthProviderRegistry()
OAUTH_PROVIDERS.register(GOOGLE_OAUTH_PROVIDER)
