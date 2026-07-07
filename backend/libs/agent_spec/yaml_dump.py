# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Stable YAML serialization for ``AgentConfigSpec`` editor display."""

from __future__ import annotations

import yaml
from libs.agent_spec import AgentConfigSpec


class _AgentSpecDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.nodes.ScalarNode:
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_AgentSpecDumper.add_representer(str, _str_representer)


def dump_agent_config_spec(spec: AgentConfigSpec) -> str:
    """Dump spec to YAML with block style for multiline strings."""
    data = spec.model_dump(mode='json', exclude_none=True, by_alias=True)
    return yaml.dump(
        data,
        Dumper=_AgentSpecDumper,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=120,
    )
