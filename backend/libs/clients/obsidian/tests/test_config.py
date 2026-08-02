# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for Obsidian tool config parsing."""

from __future__ import annotations

from typing import Any

from libs.clients.obsidian.config import ObsidianToolConfig, parse_obsidian_tool_config
from libs.clients.obsidian.errors import ObsidianConfigError

from olib.py.django.test.cases import OTestCase


class TestParseObsidianToolConfig(OTestCase):
    def test_parses_vault_and_roots(self) -> None:
        parsed = parse_obsidian_tool_config({'vault': 'Personal', 'roots': ['Journal', 'Notes']})
        self.assertEqual(parsed, ObsidianToolConfig(vault='Personal', roots=('Journal', 'Notes')))

    def test_trims_whitespace_from_vault_and_roots(self) -> None:
        parsed = parse_obsidian_tool_config({'vault': ' Personal ', 'roots': [' Journal ']})
        self.assertEqual(parsed, ObsidianToolConfig(vault='Personal', roots=('Journal',)))

    def test_rejects_non_mapping_config(self) -> None:
        configs: tuple[Any, ...] = (None, [], 'vault')
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ObsidianConfigError):
                parse_obsidian_tool_config(config)

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ObsidianConfigError):
            parse_obsidian_tool_config({'vault': 'Personal', 'roots': ['Journal'], 'extra': True})

    def test_requires_nonempty_vault(self) -> None:
        malformed: tuple[dict[str, Any], ...] = (
            {'roots': ['Journal']},
            {'vault': '', 'roots': ['Journal']},
            {'vault': '   ', 'roots': ['Journal']},
            {'vault': 1, 'roots': ['Journal']},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(ObsidianConfigError):
                parse_obsidian_tool_config(config)

    def test_requires_nonempty_roots_list(self) -> None:
        malformed: tuple[dict[str, Any], ...] = (
            {'vault': 'Personal'},
            {'vault': 'Personal', 'roots': []},
            {'vault': 'Personal', 'roots': ()},
            {'vault': 'Personal', 'roots': 'Journal'},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(ObsidianConfigError):
                parse_obsidian_tool_config(config)

    def test_rejects_blank_or_non_string_root_entries(self) -> None:
        malformed: tuple[dict[str, Any], ...] = (
            {'vault': 'Personal', 'roots': ['']},
            {'vault': 'Personal', 'roots': ['  ']},
            {'vault': 'Personal', 'roots': [1]},
            {'vault': 'Personal', 'roots': ['Journal', None]},
        )
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(ObsidianConfigError):
                parse_obsidian_tool_config(config)
