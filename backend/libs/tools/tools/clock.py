# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Clock tool — read-only UTC time for demos and smoke tests."""

from datetime import UTC, datetime

from libs.tools.base import Tool, ToolFunction


class ClockTool(Tool):
    name = 'clock'

    def functions(self) -> list[ToolFunction]:
        """Return the clock tool's LLM-visible sub-functions."""
        return [
            ToolFunction(
                name='now',
                description='Return the current UTC time as an ISO-8601 string.',
                parameters={'type': 'object', 'properties': {}, 'required': []},
                handler=self._now,
                readonly=True,
            ),
        ]

    @staticmethod
    def _now() -> str:
        """Return the current UTC timestamp in ISO-8601 form."""
        return datetime.now(UTC).isoformat()
