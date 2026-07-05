# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from libs.agent_spec.trigger_prompts import default_trigger_prompt

FROM_VERSION = 1
TO_VERSION = 2


def upgrade(raw: dict) -> dict:
    """Bump to schema v2 by inserting default trigger prompts where missing."""
    out = dict(raw)
    out['schema_version'] = TO_VERSION
    triggers_in = list(out.get('triggers') or [])
    triggers_out: list[dict] = []
    for entry in triggers_in:
        trigger = dict(entry)
        kind = trigger.get('kind')
        if kind == 'manual':
            trigger.pop('prompt', None)
        else:
            prompt = trigger.get('prompt')
            if not (prompt and str(prompt).strip()):
                default = default_trigger_prompt(str(kind))
                if default is not None:
                    trigger['prompt'] = default
        triggers_out.append(trigger)
    out['triggers'] = triggers_out
    return out
