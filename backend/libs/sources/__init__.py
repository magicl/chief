# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django-free source adapter framework."""

from libs.sources.base import PollResult, PutItemCallback, PutItemResult, SourceAdapter
from libs.sources.registry import all_adapters, get_adapter

__all__ = [
    'PollResult',
    'PutItemCallback',
    'PutItemResult',
    'SourceAdapter',
    'all_adapters',
    'get_adapter',
]
