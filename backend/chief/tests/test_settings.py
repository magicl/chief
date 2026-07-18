# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for Chief-owned Django settings."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from olib.py.django.test.cases import OTestCase


class TestCredentialSettings(OTestCase):
    """Verify Chief owns its credential-encryption key policy."""

    def test_production_requires_credentials_key_through_env_secret(self) -> None:
        """Production must report the shared secret-helper validation message."""
        process_env = {
            **os.environ,
            'DJANGO_ENV': 'production',
            'DJANGO_SECRET': 'test-only-django-secret',
        }
        process_env.pop('CREDENTIALS_KEY', None)
        repository_root = Path(__file__).resolve().parents[3]

        completed = subprocess.run(
            [
                sys.executable,
                'backend/manage.py',
                'check',
            ],
            cwd=repository_root,
            env=process_env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            'CREDENTIALS_KEY must be set when DEBUG is False',
            completed.stderr,
        )
