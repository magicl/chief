# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for DB-only config save metadata."""

from apps.agents.services.config_commands import create_from_example
from apps.agents.services.config_sync import (
    compute_save_metadata,
    spec_content_hash,
)
from django.contrib.auth import get_user_model

from olib.py.django.test.cases import OTestCase


class ConfigSyncTests(OTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='sync-user', password='secret')
        self.agent = create_from_example(self.user, 'clock-assistant', identifier='sync-agent')

    def test_compute_save_metadata_uses_ui_timestamp(self) -> None:
        source_rev, dirty = compute_save_metadata(self.agent, 'schema_version: 1\n')
        self.assertTrue(source_rev.startswith('ui:'))
        self.assertFalse(dirty)

    def test_spec_content_hash_stable(self) -> None:
        raw = 'a: 1\n'
        self.assertEqual(spec_content_hash(raw), spec_content_hash('a: 1\r\n'))
