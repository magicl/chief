# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Provider-local configuration (no Django app imports)."""

from __future__ import annotations

from pydantic import BaseModel


class ProviderLLMConfig(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
