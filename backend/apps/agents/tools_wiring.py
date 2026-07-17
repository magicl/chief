# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Register built-in tools at Django startup."""

from __future__ import annotations


def wire_tools() -> None:
    from libs.tools.registry import register_tool
    from libs.tools.tools.clickup import ClickUpTool
    from libs.tools.tools.clock import ClockTool
    from libs.tools.tools.gmail import GmailTool
    from libs.tools.tools.load_skill import LoadSkillTool
    from libs.tools.tools.queue import QueueTool

    register_tool('clock', ClockTool())
    register_tool('gmail', GmailTool())
    register_tool('clickup', ClickUpTool())
    register_tool('queue', QueueTool())
    register_tool('load_skill', LoadSkillTool())
