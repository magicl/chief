# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Shipped example agent specs for dashboard instantiation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from libs.agent_spec import AgentConfigSpec, load_spec

_EXAMPLES_DIR = Path(__file__).resolve().parent / 'examples'


def _example_path(slug: str) -> Path:
    """Resolve a safe path under ``examples/`` for *slug*."""
    if '/' in slug or '\\' in slug or slug in ('.', '..') or '..' in slug:
        raise FileNotFoundError(f'Unknown example spec {slug!r}')
    path = (_EXAMPLES_DIR / f'{slug}.yaml').resolve()
    if not path.is_relative_to(_EXAMPLES_DIR.resolve()):
        raise FileNotFoundError(f'Unknown example spec {slug!r}')
    if not path.is_file():
        raise FileNotFoundError(f'Unknown example spec {slug!r}')
    return path


_META_TITLE = re.compile(r'^#\s*title:\s*(.+)$', re.MULTILINE)
_META_DESC = re.compile(r'^#\s*description:\s*(.+)$', re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ExampleSpecInfo:
    slug: str
    title: str
    description: str


def _parse_meta(text: str) -> tuple[str | None, str | None]:
    """Parse ``# title:`` and ``# description:`` comment metadata from example YAML."""
    title_match = _META_TITLE.search(text)
    desc_match = _META_DESC.search(text)
    title = title_match.group(1).strip() if title_match else None
    description = desc_match.group(1).strip() if desc_match else None
    return title, description


def list_examples() -> list[ExampleSpecInfo]:
    """Return metadata for each ``examples/*.yaml`` file."""
    items: list[ExampleSpecInfo] = []
    for path in sorted(_EXAMPLES_DIR.glob('*.yaml')):
        text = path.read_text(encoding='utf-8')
        title, description = _parse_meta(text)
        slug = path.stem
        items.append(
            ExampleSpecInfo(
                slug=slug,
                title=title or slug.replace('-', ' ').title(),
                description=description or '',
            ),
        )
    return items


def _parse_structured_text(raw: str) -> Any:
    """Parse raw text as JSON or YAML for example spec loading."""
    stripped = raw.strip()
    if not stripped:
        raise ValueError('Spec text is empty')
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return yaml.safe_load(stripped)


def load_example_text(slug: str) -> str:
    """Return raw YAML text for an example spec."""
    return _example_path(slug).read_text(encoding='utf-8')


def load_example(slug: str) -> AgentConfigSpec:
    """Load and validate an example spec by slug (filename without ``.yaml``)."""
    return load_spec(_parse_structured_text(load_example_text(slug)))
