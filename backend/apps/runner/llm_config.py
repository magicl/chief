# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Map agent spec LLM config to provider-local types."""

from __future__ import annotations

from collections.abc import Callable

from apps.keys.exceptions import KeyStorageMisconfiguredError
from apps.keys.services.queries import make_secret_supplier
from apps.keys.types import is_registered_type

# isort: split

from libs.agent_spec import LLMSpec
from libs.providers.llm.errors import CredentialStorageMisconfigured
from libs.providers.llm.types import ProviderLLMConfig


def _wrap_secret_supplier(supplier: Callable[[], str | None]) -> Callable[[], str | None]:
    """Translate decrypt failures into provider configuration errors for the runner."""

    def wrapped() -> str | None:
        try:
            return supplier()
        except KeyStorageMisconfiguredError as exc:
            raise CredentialStorageMisconfigured() from exc

    return wrapped


def provider_config_from_spec(
    llm: LLMSpec,
    *,
    user_id: int | None,
    credential_ref: str | None = None,
) -> ProviderLLMConfig:
    """Build provider config for the runner boundary.

    When ``user_id`` is ``None``, no ``secret_supplier`` is attached and providers
    fall back to env vars. When set, credentials resolve from ``apps.keys`` at call
    time via ``make_secret_supplier``.
    """
    supplier = None
    if user_id is not None and is_registered_type(llm.provider):
        supplier = _wrap_secret_supplier(
            make_secret_supplier(
                user_id,
                name=credential_ref,
                type=llm.provider,
            ),
        )
    return ProviderLLMConfig(
        provider=llm.provider,
        model=llm.model,
        temperature=llm.temperature,
        credential_ref=credential_ref,
        user_id=user_id,
        secret_supplier=supplier,
    )
