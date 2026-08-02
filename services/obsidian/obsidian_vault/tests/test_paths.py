# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import unittest
from pathlib import Path

from obsidian_vault.paths import PathGateError, resolve_under_roots


class TestPathResolve(unittest.TestCase):
    def test_accepts_path_inside_root(self) -> None:
        base = Path('/vaults/Personal')
        got = resolve_under_roots(base, roots=['Journal'], rel_path='Journal/2026-08-02.md')
        self.assertEqual(got, base / 'Journal' / '2026-08-02.md')

    def test_rejects_escape_via_dotdot(self) -> None:
        base = Path('/vaults/Personal')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='Journal/../Secrets/x.md')

    def test_rejects_outside_configured_roots(self) -> None:
        base = Path('/vaults/Personal')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='Other/note.md')
