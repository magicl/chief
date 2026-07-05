# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Default bootstrap prompts for trigger kinds (schema v1 → v2 migration and runtime fallback)."""

DEFAULT_SCHEDULE_TRIGGER_PROMPT = 'Scheduled run started. Execute your configured tasks.'
DEFAULT_QUEUE_TRIGGER_PROMPT = 'Process this queue item.'
DEFAULT_AGENT_TRIGGER_PROMPT = 'Agent trigger run started.'

_DEFAULT_PROMPTS_BY_KIND = {
    'schedule': DEFAULT_SCHEDULE_TRIGGER_PROMPT,
    'queue': DEFAULT_QUEUE_TRIGGER_PROMPT,
    'agent': DEFAULT_AGENT_TRIGGER_PROMPT,
}


def default_trigger_prompt(kind: str) -> str | None:
    """Return the legacy default prompt for *kind*, or ``None`` for manual triggers."""
    return _DEFAULT_PROMPTS_BY_KIND.get(kind)
