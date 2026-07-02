# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Canonical in-code tool instances (like ``PROVIDERS`` for LLM backends)."""

from __future__ import annotations

from libs.tools.base import Tool

TOOLS: dict[str, Tool] = {}


def register_tool(name: str, tool: Tool) -> None:
    TOOLS[name] = tool


def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)


def all_tools() -> dict[str, Tool]:
    return dict(TOOLS)
