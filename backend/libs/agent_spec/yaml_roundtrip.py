# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Round-trip YAML load/dump that preserves comments and formatting."""

from __future__ import annotations

from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml_engine() -> YAML:
    """Return a configured ruamel YAML engine for editor round-trips."""
    engine = YAML()
    engine.preserve_quotes = True
    engine.indent(mapping=2, sequence=4, offset=2)
    engine.width = 120
    return engine


def load_yaml_document(raw: str) -> CommentedMap:
    """Parse YAML text into a mutable document that retains comments."""
    engine = _yaml_engine()
    doc = engine.load(raw)
    if not isinstance(doc, CommentedMap):
        raise ValueError('agent config YAML must be a mapping at the top level')
    return doc


def dump_yaml_document(doc: CommentedMap) -> str:
    """Serialize a ruamel document back to YAML text."""
    engine = _yaml_engine()
    stream = StringIO()
    engine.dump(doc, stream)
    return stream.getvalue()


def plain_dict(value: Any) -> Any:
    """Recursively convert ruamel containers to plain Python collections."""
    if isinstance(value, CommentedMap):
        return {key: plain_dict(item) for key, item in value.items()}
    if isinstance(value, CommentedSeq):
        return [plain_dict(item) for item in value]
    if isinstance(value, list):
        return [plain_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: plain_dict(item) for key, item in value.items()}
    return value
