# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Discovery registry for source adapters."""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
from pathlib import Path

from libs.sources.base import SourceAdapter


def _discover_adapters() -> dict[str, SourceAdapter]:
    """Import ``libs.sources.adapters.*`` and instantiate one adapter per concrete class."""
    adapters_pkg = importlib.import_module('libs.sources.adapters')
    pkg_file = adapters_pkg.__file__
    if pkg_file is None:
        raise RuntimeError('libs.sources.adapters package has no __file__')
    pkg_path = Path(pkg_file).parent
    adapters: dict[str, SourceAdapter] = {}
    for info in sorted(pkgutil.iter_modules([str(pkg_path)])):
        if info.name.startswith('_'):
            continue
        module = importlib.import_module(f'libs.sources.adapters.{info.name}')
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, SourceAdapter) or obj is SourceAdapter:
                continue
            instance = obj()
            adapter_type = instance.adapter_type
            if adapter_type in adapters:
                raise RuntimeError(f'duplicate source adapter type {adapter_type!r}')
            adapters[adapter_type] = instance
    return adapters


@functools.cache
def _cached_adapters() -> dict[str, SourceAdapter]:
    return _discover_adapters()


def get_adapter(adapter_type: str) -> SourceAdapter | None:
    """Return the registered adapter for *adapter_type*, or ``None`` if unknown."""
    return _cached_adapters().get(adapter_type)


def all_adapters() -> dict[str, SourceAdapter]:
    """Return a copy of all registered source adapters keyed by type name."""
    return dict(_cached_adapters())
