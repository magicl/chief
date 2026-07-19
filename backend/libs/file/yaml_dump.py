# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shared human-editable YAML serialization."""

from __future__ import annotations

from typing import Any

import yaml


class _EditableYamlDumper(yaml.SafeDumper):
    """Serialize YAML values using editor-friendly scalar styles."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    """Represent multiline strings as literal blocks for easier editing."""
    # YAML block scalars normalize non-LF line-break characters, so retain
    # quoted serialization whenever literal style would change the value.
    has_normalized_break = any(char in data for char in '\r\x85\u2028\u2029')
    style: str | None
    if has_normalized_break:
        style = '"'
    else:
        style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)


_EditableYamlDumper.add_representer(str, _str_representer)


def dump_editable_yaml(
    data: Any,
    *,
    sort_keys: bool = True,
    width: int = 80,
) -> str:
    """Dump YAML with stable formatting suited to text editor surfaces."""
    return yaml.dump(
        data,
        Dumper=_EditableYamlDumper,
        sort_keys=sort_keys,
        default_flow_style=False,
        allow_unicode=True,
        width=width,
    )
