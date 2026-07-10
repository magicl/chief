# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Provider-local configuration (no Django app imports)."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict


class ProviderLLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    model: str
    temperature: float | None = None
    credential_ref: str | None = None
    user_id: int | None = None
    secret_supplier: Callable[[], str | None] | None = None
