# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent configuration spec schema, migrations, and shipped examples."""

from __future__ import annotations

from libs.agent_spec.example_catalog import (
    ExampleSpecInfo,
    list_examples,
    load_example,
    load_example_text,
)
from libs.agent_spec.exceptions import (
    SpecMigrationError,
    UnsupportedSpecVersionError,
)
from libs.agent_spec.loader import (
    detect_version,
    load_spec,
    load_spec_dict,
)
from libs.agent_spec.registry import get_spec_migrations, latest_spec_version
from libs.agent_spec.spec import (
    AGENT_CONFIG_SPEC_VERSION,
    AgentConfigSpec,
    IntegrationSpec,
    LLMSpec,
    QueueSpec,
    SourceSpec,
    ToolInstance,
    TriggerSpec,
)

__all__ = [
    'AGENT_CONFIG_SPEC_VERSION',
    'AgentConfigSpec',
    'ExampleSpecInfo',
    'IntegrationSpec',
    'LLMSpec',
    'QueueSpec',
    'SourceSpec',
    'ToolInstance',
    'TriggerSpec',
    'SpecMigrationError',
    'UnsupportedSpecVersionError',
    'detect_version',
    'get_spec_migrations',
    'latest_spec_version',
    'list_examples',
    'load_example',
    'load_example_text',
    'load_spec',
    'load_spec_dict',
]
