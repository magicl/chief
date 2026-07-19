# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Canonical OAuth capability, provider, and registry exports."""

from apps.keys.oauth.registry import OAUTH_PROVIDERS, OAuthProviderRegistry
from apps.keys.oauth.types import OAuthCapability, OAuthProvider, OAuthSupport

__all__ = [
    'OAUTH_PROVIDERS',
    'OAuthCapability',
    'OAuthProvider',
    'OAuthProviderRegistry',
    'OAuthSupport',
]
