# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import contextlib
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from obsidian_vault.bindings import SyncPendingError, VaultBindingStore
from obsidian_vault.files import VaultFileService
from obsidian_vault.paths import PathGateError, open_file_under_roots


class TestVaultFileService(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.vault_root = Path(stack.enter_context(TemporaryDirectory()))
        self.store = VaultBindingStore()
        self.store.ensure_agent(
            'agent-1',
            [{'vault_id': 'Personal', 'roots': ['Journal'], 'credential': {'auth_token': 'tok'}}],
        )
        self.files = VaultFileService(self.store, vault_root_for=lambda vault_id: self.vault_root)

    def _mark_ready(self) -> None:
        self.store.mark_vault_ready('Personal')

    def test_read_before_ready_raises_sync_pending(self) -> None:
        with self.assertRaises(SyncPendingError):
            self.files.read_text('agent-1', 'Personal', 'Journal/note.md')

    def test_list_dir_before_ready_raises_sync_pending(self) -> None:
        with self.assertRaises(SyncPendingError):
            self.files.list_dir('agent-1', 'Personal', 'Journal')

    def test_write_then_read_roundtrip(self) -> None:
        self._mark_ready()
        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'hello')
        self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/note.md'), 'hello')

    def test_write_creates_parent_dirs(self) -> None:
        self._mark_ready()
        self.files.write_text('agent-1', 'Personal', 'Journal/2026/08/note.md', 'hi')
        self.assertTrue((self.vault_root / 'Journal' / '2026' / '08' / 'note.md').exists())

    def test_write_overwrites_existing_content(self) -> None:
        self._mark_ready()
        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'first')
        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'second')
        self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/note.md'), 'second')

    def test_append_creates_file_and_parents_when_missing(self) -> None:
        self._mark_ready()
        self.files.append_text('agent-1', 'Personal', 'Journal/2026/note.md', 'line1\n')
        self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/2026/note.md'), 'line1\n')

    def test_append_adds_to_existing_content(self) -> None:
        self._mark_ready()
        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'line1\n')
        self.files.append_text('agent-1', 'Personal', 'Journal/note.md', 'line2\n')
        self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/note.md'), 'line1\nline2\n')

    def test_list_dir_returns_sorted_names_non_recursive(self) -> None:
        self._mark_ready()
        self.files.write_text('agent-1', 'Personal', 'Journal/b.md', 'b')
        self.files.write_text('agent-1', 'Personal', 'Journal/a.md', 'a')
        (self.vault_root / 'Journal' / 'sub').mkdir(parents=True)
        (self.vault_root / 'Journal' / 'sub' / 'nested.md').write_text('nested', encoding='utf-8')
        self.assertEqual(
            self.files.list_dir('agent-1', 'Personal', 'Journal'),
            ['a.md', 'b.md', 'sub'],
        )

    def test_write_outside_configured_roots_raises_path_gate(self) -> None:
        self._mark_ready()
        with self.assertRaises(PathGateError):
            self.files.write_text('agent-1', 'Personal', 'Secrets/x.md', 'nope')

    def test_read_escape_via_dotdot_raises_path_gate(self) -> None:
        self._mark_ready()
        with self.assertRaises(PathGateError):
            self.files.read_text('agent-1', 'Personal', 'Journal/../Secrets/x.md')

    def test_read_missing_file_raises_file_not_found(self) -> None:
        self._mark_ready()
        with self.assertRaises(FileNotFoundError):
            self.files.read_text('agent-1', 'Personal', 'Journal/missing.md')

    def test_two_agents_sharing_a_vault_cannot_cross_access_each_others_roots(self) -> None:
        self._mark_ready()
        self.store.ensure_agent(
            'agent-2',
            [{'vault_id': 'Personal', 'roots': ['Inbox'], 'credential': {'auth_token': 'tok2'}}],
        )

        self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'agent-1 content')
        self.files.write_text('agent-2', 'Personal', 'Inbox/item.md', 'agent-2 content')

        with self.assertRaises(PathGateError):
            self.files.read_text('agent-2', 'Personal', 'Journal/note.md')
        with self.assertRaises(PathGateError):
            self.files.write_text('agent-2', 'Personal', 'Journal/note.md', 'nope')
        with self.assertRaises(PathGateError):
            self.files.read_text('agent-1', 'Personal', 'Inbox/item.md')

        # Both agents still see their own root's content in the shared checkout.
        self.assertEqual(self.files.read_text('agent-1', 'Personal', 'Journal/note.md'), 'agent-1 content')
        self.assertEqual(self.files.read_text('agent-2', 'Personal', 'Inbox/item.md'), 'agent-2 content')

    def test_write_holds_per_vault_lock_during_io(self) -> None:
        self._mark_ready()
        lock = self.store.lock_for('Personal')
        observed_locked_state: dict[str, bool] = {}
        original_open = open_file_under_roots

        def spy_open(*args: object, **kwargs: object) -> int:
            observed_locked_state['locked'] = lock.locked()
            return original_open(*args, **kwargs)  # type: ignore[arg-type]

        with unittest.mock.patch('obsidian_vault.files.open_file_under_roots', spy_open):
            self.files.write_text('agent-1', 'Personal', 'Journal/note.md', 'hi')

        self.assertTrue(observed_locked_state['locked'])
        self.assertFalse(lock.locked())

    def test_read_rejects_symlink_leaf(self) -> None:
        """Symlink leaf under an allowed root must raise PathGateError, not leak content."""
        self._mark_ready()
        journal = self.vault_root / 'Journal'
        journal.mkdir(parents=True)
        secret = self.vault_root / 'Secrets'
        secret.mkdir()
        target = secret / 'x.md'
        target.write_text('secret', encoding='utf-8')
        (journal / 'note.md').symlink_to(target)

        with self.assertRaises(PathGateError):
            self.files.read_text('agent-1', 'Personal', 'Journal/note.md')
