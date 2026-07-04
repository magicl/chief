# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from libs.agent_spec.exceptions import SpecMigrationError

FROM_VERSION = 0
TO_VERSION = 1


def upgrade(raw: dict) -> dict:
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    tools_in = list(out.get('tools') or [])
    seen_ids: set[str] = set()
    tools_out: list[dict] = []
    for entry in tools_in:
        tool_name = entry.get('tool')
        if not tool_name:
            raise SpecMigrationError('v0 tool entry missing tool name')
        if tool_name in seen_ids:
            raise SpecMigrationError(f"duplicate tool {tool_name!r} — add explicit instance ids in v1")
        seen_ids.add(tool_name)
        tools_out.append(
            {
                'id': tool_name,
                'type': tool_name,
                'allow': list(entry.get('allow') or ['*']),
                'deny': list(entry.get('deny') or []),
            }
        )
    out['tools'] = tools_out
    return out
