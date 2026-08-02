# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Fail-fast startup contract for the vault service ASGI entrypoint module."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[2]


def _run_import(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import `obsidian_vault.main` in a subprocess with a minimal, explicit env.

    Isolated from the current process's ambient env (which may already have
    `OBSIDIAN_VAULT_TOKEN` set) so each case only sees what it declares.
    """
    with tempfile.TemporaryDirectory() as data_dir:
        env = {'PATH': os.environ.get('PATH', ''), 'OBSIDIAN_VAULT_DATA': data_dir}
        env.update(env_overrides)
        return subprocess.run(  # noqa: S603
            [sys.executable, '-c', 'import obsidian_vault.main'],
            cwd=_SERVICE_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )


class TestMainFailsFastOnEmptyToken(unittest.TestCase):
    """Verify the ASGI entrypoint refuses to start without a real inter-service token."""

    def test_missing_token_raises_at_import(self) -> None:
        """No `OBSIDIAN_VAULT_TOKEN` at all exits non-zero with a clear message."""
        result = _run_import({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OBSIDIAN_VAULT_TOKEN', result.stderr)

    def test_blank_token_raises_at_import(self) -> None:
        """An explicitly blank `OBSIDIAN_VAULT_TOKEN` is treated the same as unset."""
        result = _run_import({'OBSIDIAN_VAULT_TOKEN': ''})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OBSIDIAN_VAULT_TOKEN', result.stderr)

    def test_configured_token_imports_successfully(self) -> None:
        """A non-empty token lets the module import (and build the app) without error."""
        result = _run_import({'OBSIDIAN_VAULT_TOKEN': 'real-service-token'})
        self.assertEqual(result.returncode, 0, result.stderr)
