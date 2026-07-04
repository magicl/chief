# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for file-backed config sync and save metadata."""

import tempfile
from pathlib import Path

from apps.agents.ingest import persist_agent_config
from apps.agents.models import Agent
from apps.agents.services.config_commands import (
    clear_file_source,
    create_from_example,
    set_file_source,
    sync_from_file,
)
from apps.agents.services.config_sync import (
    ConfigSyncError,
    compute_save_metadata,
    spec_content_hash,
)
from django.contrib.auth import get_user_model
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example

from olib.py.django.test.cases import OTestCase


class ConfigSyncTests(OTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='sync-user', password='secret')
        self.agent = create_from_example(self.user, 'clock-assistant', identifier='sync-agent')

    def test_compute_save_metadata_ui_source_uses_timestamp(self) -> None:
        source_rev, dirty = compute_save_metadata(self.agent, 'schema_version: 1\n')
        self.assertTrue(source_rev.startswith('ui:'))
        self.assertFalse(dirty)

    def test_compute_save_metadata_file_backed_sets_dirty_when_edited(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as handle:
            handle.write(spec_yaml)
            path = handle.name
        try:
            set_file_source(self.agent, path, sync_now=True)
            edited = spec_yaml + '\n# edited\n'
            source_rev, dirty = compute_save_metadata(self.agent, edited)
            self.assertTrue(source_rev.startswith('ui-sha256:'))
            self.assertTrue(dirty)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_compute_save_metadata_raises_when_file_missing(self) -> None:
        self.agent.config_source = 'file:/tmp/does-not-exist-chief-config.yaml'
        self.agent.save(update_fields=['config_source'])
        with self.assertRaises(ConfigSyncError):
            compute_save_metadata(self.agent, 'schema_version: 1\n')

    def test_sync_from_file_is_idempotent(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as handle:
            handle.write(spec_yaml)
            path = handle.name
        try:
            set_file_source(self.agent, path, sync_now=True)
            before = Agent.objects.get(pk=self.agent.pk).current_config_id
            result = sync_from_file(self.agent)
            self.assertIsNone(result)
            self.agent.refresh_from_db()
            self.assertEqual(self.agent.current_config_id, before)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_sync_from_file_persists_when_content_changes(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as handle:
            handle.write(spec_yaml)
            path = handle.name
        try:
            set_file_source(self.agent, path, sync_now=True)
            self.agent.refresh_from_db()
            before = self.agent.current_config_id
            updated = spec_yaml.replace('Clock assistant example', 'Clock assistant changed')
            self.assertNotEqual(updated, spec_yaml)
            Path(path).write_text(updated, encoding='utf-8')
            result = sync_from_file(self.agent)
            self.assertIsNotNone(result)
            self.agent.refresh_from_db()
            self.assertNotEqual(self.agent.current_config_id, before)
            config = self.agent.current_config
            assert config is not None
            self.assertFalse(config.dirty)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_clear_file_source_clears_dirty(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('clock-assistant'))
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as handle:
            handle.write(spec_yaml)
            path = handle.name
        try:
            set_file_source(self.agent, path, sync_now=True)
            persist_agent_config(
                self.agent,
                load_example('clock-assistant'),
                source_rev='ui-sha256:test',
                dirty=True,
            )
            config = self.agent.current_config
            assert config is not None
            self.assertTrue(config.dirty)
            clear_file_source(self.agent)
            self.agent.refresh_from_db()
            self.assertEqual(self.agent.config_source, 'ui')
            config = self.agent.current_config
            assert config is not None
            self.assertFalse(config.dirty)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_set_file_source_rejects_relative_path(self) -> None:
        from apps.agents.services.config_commands import ConfigCommandError

        with self.assertRaises(ConfigCommandError):
            set_file_source(self.agent, 'relative/path.yaml')

    def test_spec_content_hash_stable(self) -> None:
        raw = 'a: 1\n'
        self.assertEqual(spec_content_hash(raw), spec_content_hash('a: 1\r\n'))
