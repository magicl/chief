# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Register built-in tools at Django startup."""

from __future__ import annotations


def wire_tools() -> None:
    from libs.tools.tools.clock import ClockTool
    from libs.tools.tools.queue import QueueTool
    from libs.tools.registry import register_tool

    register_tool('clock', ClockTool())
    register_tool('queue', QueueTool())
