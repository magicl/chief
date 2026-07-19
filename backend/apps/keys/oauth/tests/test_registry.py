# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the generic OAuth provider registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

from apps.keys.exceptions import KeyValidationError
from apps.keys.oauth.registry import OAUTH_PROVIDERS, OAuthProviderRegistry
from apps.keys.oauth.types import OAuthCapability

from olib.py.django.test.cases import OTestCase


class _StubProvider:
    """Provide the smallest complete provider implementation for registry tests."""

    id = 'stub'
    credential_type = 'stub-key'
    capabilities: tuple[OAuthCapability, ...] = ()

    def normalize_capabilities(self, capability_ids: object) -> tuple[str, ...]:
        """Return no capabilities because this stub has no catalog."""
        del capability_ids
        return ()

    def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Return a fixed URL because transport behavior is outside registry tests."""
        del redirect_uri, state, capability_ids
        return 'https://example.test/authorize'

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        capability_ids: tuple[str, ...],
    ) -> str:
        """Return a fixed grant because exchange behavior is outside registry tests."""
        del code, redirect_uri, capability_ids
        return '{}'

    def materialize_runtime(self, *, grant_payload: str, capability_ids: tuple[str, ...]) -> str:
        """Return a fixed runtime value because materialization is outside registry tests."""
        del grant_payload, capability_ids
        return '{}'


class TestOAuthCapability(OTestCase):
    def test_record_is_frozen_and_slotted(self) -> None:
        capability = OAuthCapability(
            id='read',
            label='Read',
            description='Read records.',
            scope='scope:read',
            support='current',
        )

        with self.assertRaises(FrozenInstanceError):
            capability.label = 'Changed'  # type: ignore[misc]
        self.assertFalse(hasattr(capability, '__dict__'))


class TestOAuthProviderRegistry(OTestCase):
    def test_register_and_get_provider(self) -> None:
        registry = OAuthProviderRegistry()
        provider = _StubProvider()

        registry.register(provider)

        self.assertIs(registry.get('stub'), provider)

    def test_duplicate_provider_id_is_rejected_safely(self) -> None:
        registry = OAuthProviderRegistry()
        provider = _StubProvider()
        registry.register(provider)

        with self.assertRaisesRegex(KeyValidationError, r'^OAuth provider is already registered$'):
            registry.register(provider)

    def test_unknown_provider_id_is_rejected_safely(self) -> None:
        registry = OAuthProviderRegistry()

        with self.assertRaisesRegex(KeyValidationError, r'^Unknown OAuth provider$'):
            registry.get('provider-secret-sentinel')

    def test_global_registry_contains_one_google_singleton(self) -> None:
        google = OAUTH_PROVIDERS.get('google')

        self.assertEqual(google.id, 'google')
        self.assertEqual(google.credential_type, 'google')
        self.assertEqual(OAUTH_PROVIDERS.provider_ids(), ('google',))
