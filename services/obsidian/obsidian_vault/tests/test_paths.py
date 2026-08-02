# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import os
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from obsidian_vault.paths import (
    PathGateError,
    list_dir_under_roots,
    open_file_under_roots,
    resolve_under_roots,
)


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

    def test_rejects_absolute_and_control_characters(self) -> None:
        base = Path('/vaults/Personal')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='/Journal/note.md')
        with self.assertRaises(PathGateError):
            resolve_under_roots(base, roots=['Journal'], rel_path='Journal/no\nte.md')


class TestPathNofollowIo(unittest.TestCase):
    def test_open_read_rejects_symlink_leaf(self) -> None:
        """A symlink at the final path component must not be followed."""
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            journal = vault / 'Journal'
            journal.mkdir()
            secret = vault / 'Secrets'
            secret.mkdir()
            target = secret / 'x.md'
            target.write_text('secret', encoding='utf-8')
            (journal / 'note.md').symlink_to(target)

            with self.assertRaises(PathGateError):
                open_file_under_roots(vault, roots=['Journal'], rel_path='Journal/note.md', flags=os.O_RDONLY)

    def test_open_read_rejects_symlink_intermediate(self) -> None:
        """A symlink replacing an intermediate directory must not be followed."""
        with TemporaryDirectory() as vault_tmp, TemporaryDirectory() as outside_tmp:
            vault = Path(vault_tmp)
            outside = Path(outside_tmp)
            (outside / 'trap.md').write_text('trap', encoding='utf-8')
            (vault / 'Journal').symlink_to(outside)

            with self.assertRaises(PathGateError):
                open_file_under_roots(vault, roots=['Journal'], rel_path='Journal/trap.md', flags=os.O_RDONLY)

    def test_write_creates_regular_file_without_following_symlink_parent(self) -> None:
        """Parent creation + write succeeds for a normal tree under roots."""
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            fd = open_file_under_roots(
                vault,
                roots=['Journal'],
                rel_path='Journal/2026/note.md',
                flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                create_parents=True,
            )
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write('ok')
            self.assertEqual((vault / 'Journal' / '2026' / 'note.md').read_text(encoding='utf-8'), 'ok')

    def test_list_dir_rejects_symlink_directory(self) -> None:
        """Listing must refuse when the directory path is a symlink."""
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            real = vault / 'Real'
            real.mkdir()
            (real / 'a.md').write_text('a', encoding='utf-8')
            (vault / 'Journal').symlink_to(real)
            with self.assertRaises(PathGateError):
                list_dir_under_roots(vault, roots=['Journal'], rel_path='Journal')
