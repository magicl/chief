# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for synchronizing local agent YAML files."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp

from apps.agents.models import Agent, AgentConfig, AgentStatus, Trigger
from apps.agents.services.schedule_beat import periodic_task_name
from apps.local_disk.agent_sync import sync_agents_dir
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_celery_beat.models import PeriodicTask

from olib.py.django.test.cases import OTestCase


class TestAgentSync(OTestCase):
    def setUp(self) -> None:
        """Create an isolated configured local root and agent owner."""
        super().setUp()
        self.root = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.agents_path = self.root / 'agents'
        self.agents_path.mkdir()
        self.settings_override = override_settings(CHIEF_LOCAL_DIR=str(self.root))
        self.settings_override.enable()
        self.user = get_user_model().objects.create_user(username='alice', email='alice@example.com')

    def tearDown(self) -> None:
        """Restore settings and remove the isolated local root."""
        self.settings_override.disable()
        super().tearDown()

    def write_agent(
        self,
        *,
        identifier: str = 'daily-helper',
        name: str = 'Daily Helper',
        prompt: str = 'Help daily.',
        cron: str | None = None,
    ) -> Path:
        """Write one valid agent YAML file and return its path."""
        trigger = (
            f"""triggers:
  - name: sweep
    kind: schedule
    cron: '{cron}'
    prompt: Run scheduled work.
"""
            if cron is not None
            else 'triggers: []\n'
        )
        path = self.agents_path / f'{identifier}.yaml'
        path.write_text(
            f"""owner: alice
identifier: {identifier}
name: {name}
schema_version: 2
llm:
  provider: openai
  model: gpt-5.4-mini
system_prompt: {prompt}
{trigger}tools: []
queues: []
""",
            encoding='utf-8',
        )
        return path

    def test_create_sets_disk_provenance_and_revision(self) -> None:
        self.write_agent()

        report = sync_agents_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        self.assertEqual(agent.config_source, 'disk')
        self.assertEqual(agent.source_path, 'agents/daily-helper.yaml')
        self.assertEqual(agent.status, AgentStatus.ACTIVE)
        config = agent.current_config
        assert config is not None
        self.assertTrue(config.source_rev.startswith('sha256:'))
        self.assertFalse(config.dirty)
        self.assertNotIn('owner:', config.spec_yaml)

    def test_content_change_creates_revision_and_rematerializes(self) -> None:
        path = self.write_agent(prompt='First prompt.', cron='0 * * * *')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        old_config_id = agent.current_config_id
        old_trigger_id = Trigger.objects.get(agent=agent, agent_config_id=old_config_id).id
        path.write_text(path.read_text(encoding='utf-8').replace('First prompt.', 'Second prompt.'), encoding='utf-8')

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 0)
        agent.refresh_from_db()
        self.assertNotEqual(agent.current_config_id, old_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 2)
        self.assertFalse(Trigger.objects.filter(id=old_trigger_id, agent_config=agent.current_config).exists())
        assert agent.current_config is not None
        self.assertEqual(agent.current_config.get_spec().system_prompt, 'Second prompt.')

    def test_unchanged_content_does_not_create_revision(self) -> None:
        self.write_agent()
        sync_agents_dir()

        report = sync_agents_dir()

        self.assertEqual(report.failed, 0)
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)

    def test_database_owned_conflict_records_failure_without_change(self) -> None:
        agent = Agent.objects.create(
            user=self.user,
            identifier='daily-helper',
            name='Database Helper',
            config_source='ui',
        )
        self.write_agent()

        with self.assertLogs('apps.local_disk.agent_sync', level='ERROR'):
            report = sync_agents_dir()

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.name, 'Database Helper')
        self.assertEqual(agent.config_source, 'ui')
        self.assertIsNone(agent.current_config)

    def test_removed_file_soft_disables_agent_and_schedule_beat(self) -> None:
        path = self.write_agent(cron='0 * * * *')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        trigger = Trigger.objects.get(agent=agent, agent_config=agent.current_config)
        task_name = periodic_task_name(trigger.id)
        self.assertTrue(PeriodicTask.objects.get(name=task_name).enabled)
        path.unlink()

        report = sync_agents_dir()

        self.assertEqual(report.disabled, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.status, AgentStatus.DISABLED)
        self.assertFalse(PeriodicTask.objects.get(name=task_name).enabled)

    def test_bad_yaml_keeps_last_good_config_active(self) -> None:
        path = self.write_agent(prompt='Last good prompt.')
        sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        old_config_id = agent.current_config_id
        path.write_text('owner: alice\nllm: [not valid\n', encoding='utf-8')

        with self.assertLogs('apps.local_disk.agent_sync', level='ERROR'):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.status, AgentStatus.ACTIVE)
        self.assertEqual(agent.current_config_id, old_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
