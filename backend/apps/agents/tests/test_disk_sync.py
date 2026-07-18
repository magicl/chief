# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock, call, patch

from apps.agents.models import Agent, AgentConfig, AgentStatus, Trigger
from apps.agents.services.disk_sync import (
    soft_disable_missing_disk_agents,
    sync_agents_dir,
)
from apps.agents.services.schedule_beat import periodic_task_name
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_celery_beat.models import PeriodicTask

from olib.py.django.test.cases import OTestCase


class TestAgentDiskSync(OTestCase):
    """Verify synchronization of disk-backed agent data."""

    def setUp(self) -> None:
        """Create an isolated configured local root and agent owner."""
        super().setUp()
        self.root = Path(mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.agents_path = self.root / 'agents'
        self.agents_path.mkdir()
        self.settings_override = override_settings(CHIEF_LOCAL_DIR=str(self.root))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(username='alice', email='alice@example.com')

    def write_agent(
        self,
        *,
        filename: str | None = None,
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
        path = self.agents_path / (filename or f'{identifier}.yaml')
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

    def test_progress_checkpoints_surround_files_and_precede_disable(self) -> None:
        """Invoke generic maintenance around file work and before missing-file disable."""
        path = self.write_agent()
        calls: list[str] = []

        def sync_file(*_args: object, **_kwargs: object) -> MagicMock:
            """Record one file synchronization."""
            calls.append('file')
            return MagicMock()

        def disable_missing(**_kwargs: object) -> tuple[int, set[int]]:
            """Record the final missing-file reconciliation."""
            calls.append('disable')
            return 0, set()

        def record_progress() -> None:
            """Record one generic maintenance checkpoint."""
            calls.append('progress')

        with (
            patch(
                'apps.agents.services.disk_sync.sync_agent_path',
                side_effect=sync_file,
            ),
            patch(
                'apps.agents.services.disk_sync.soft_disable_missing_disk_agents',
                side_effect=disable_missing,
            ),
        ):
            sync_agents_dir(progress=record_progress)

        self.assertTrue(path.exists())
        self.assertEqual(
            calls,
            ['progress', 'file', 'progress', 'progress', 'disable'],
        )

    @patch('apps.agents.services.disk_sync.sync_agent_schedule_triggers')
    def test_missing_agents_checkpoint_each_schedule_sync(self, sync_schedule: MagicMock) -> None:
        """Surround every missing agent's schedule reconciliation with checkpoints."""
        self.write_agent(identifier='first', name='First')
        self.write_agent(identifier='second', name='Second')
        sync_agents_dir()
        agent_ids = list(Agent.objects.filter(user=self.user).order_by('identifier').values_list('id', flat=True))
        progress = MagicMock()

        soft_disable_missing_disk_agents(present_paths=set(), progress=progress)

        self.assertEqual(progress.call_count, 4)
        sync_schedule.assert_has_calls(
            [call(agent_id, progress=progress) for agent_id in agent_ids],
            any_order=True,
        )
        self.assertEqual(sync_schedule.call_count, 2)

    def test_checkpoint_failure_rolls_back_agent_and_schedule_mutations(self) -> None:
        """Roll back status and Beat updates when lease maintenance fails mid-agent."""
        self.write_agent(identifier='first', name='First', cron='0 * * * *')
        self.write_agent(identifier='second', name='Second', cron='5 * * * *')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent_ids = list(Agent.objects.filter(user=self.user).values_list('id', flat=True))
        task_names = [
            periodic_task_name(trigger_id)
            for trigger_id in Trigger.objects.filter(agent_id__in=agent_ids).values_list('id', flat=True)
        ]
        checkpoint_count = 0

        def fail_after_schedule_mutation() -> None:
            """Fail after the first trigger's Beat row has been updated."""
            nonlocal checkpoint_count
            checkpoint_count += 1
            if checkpoint_count == 3:
                raise RuntimeError('lease renewal failed')

        with self.assertRaises(RuntimeError):
            soft_disable_missing_disk_agents(
                present_paths=set(),
                progress=fail_after_schedule_mutation,
            )

        self.assertEqual(
            set(Agent.objects.filter(id__in=agent_ids).values_list('status', flat=True)),
            {AgentStatus.ACTIVE},
        )
        tasks = list(PeriodicTask.objects.filter(name__in=task_names))
        self.assertEqual(checkpoint_count, 3)
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(task.enabled for task in tasks))

    @patch('apps.bus.resources.publish_resource_update')
    def test_create_sets_disk_provenance_and_revision(
        self,
        publish: MagicMock,
    ) -> None:
        """Create a disk-backed agent with a clean hashed revision."""
        self.write_agent()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.succeeded, 1)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'agents')
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        self.assertEqual(agent.config_source, 'disk')
        self.assertEqual(agent.source_path, 'agents/daily-helper.yaml')
        self.assertEqual(agent.status, AgentStatus.ACTIVE)
        config = agent.current_config
        assert config is not None
        self.assertTrue(config.source_rev.startswith('sha256:'))
        self.assertFalse(config.dirty)
        self.assertNotIn('owner:', config.spec_yaml)

    @patch('apps.bus.resources.publish_resource_update')
    def test_content_change_creates_revision_and_rematerializes(
        self,
        publish: MagicMock,
    ) -> None:
        """Persist changed content as a new materialized config revision."""
        path = self.write_agent(prompt='First prompt.', cron='0 * * * *')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        publish.reset_mock()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        old_config_id = agent.current_config_id
        old_trigger_id = Trigger.objects.get(agent=agent, agent_config_id=old_config_id).id
        path.write_text(path.read_text(encoding='utf-8').replace('First prompt.', 'Second prompt.'), encoding='utf-8')

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'agents')
        agent.refresh_from_db()
        self.assertNotEqual(agent.current_config_id, old_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 2)
        self.assertFalse(Trigger.objects.filter(id=old_trigger_id, agent_config=agent.current_config).exists())
        assert agent.current_config is not None
        self.assertEqual(agent.current_config.get_spec().system_prompt, 'Second prompt.')

    @patch('apps.bus.resources.publish_resource_update')
    def test_unchanged_content_does_not_create_revision(self, publish: MagicMock) -> None:
        """Avoid duplicate revisions when disk content is unchanged."""
        self.write_agent()
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, set())
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertFalse(report.items[0].changed)
        publish.assert_not_called()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)

    def test_database_owned_conflict_records_failure_without_change(self) -> None:
        """Contain ownership conflicts without changing database-owned agents."""
        agent = Agent.objects.create(
            user=self.user,
            identifier='daily-helper',
            name='Database Helper',
            config_source='ui',
        )
        self.write_agent()

        with self.assertLogs('apps.agents.services.disk_sync', level='ERROR'):
            report = sync_agents_dir()

        self.assertEqual(report.succeeded, 0)
        self.assertEqual(report.failed, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.name, 'Database Helper')
        self.assertEqual(agent.config_source, 'ui')
        self.assertIsNone(agent.current_config)

    @patch('apps.bus.resources.publish_resource_update')
    def test_duplicate_identity_keeps_first_file_without_churn(self, publish: MagicMock) -> None:
        """Reject later duplicate identities and keep the first file authoritative."""
        self.write_agent(filename='a-first.yaml', prompt='First prompt.')
        self.write_agent(filename='b-second.yaml', prompt='Second duplicate prompt.')

        with self.assertLogs('apps.agents.services.disk_sync', level='ERROR') as captured:
            with self.captureOnCommitCallbacks(execute=True):
                first_report = sync_agents_dir()

        self.assertEqual(first_report.succeeded, 1)
        self.assertEqual(first_report.failed, 1)
        self.assertEqual(
            {item.detail for item in first_report.items if not item.success},
            {'duplicate identity'},
        )
        self.assertNotIn('Second duplicate prompt.', '\n'.join(captured.output))
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        self.assertEqual(agent.source_path, 'agents/a-first.yaml')
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
        assert agent.current_config is not None
        self.assertEqual(agent.current_config.get_spec().system_prompt, 'First prompt.')
        publish.assert_called_once_with(self.user.pk, 'agents')
        publish.reset_mock()

        with self.assertLogs('apps.agents.services.disk_sync', level='ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                second_report = sync_agents_dir()

        agent.refresh_from_db()
        self.assertEqual(second_report.succeeded, 1)
        self.assertEqual(second_report.failed, 1)
        self.assertEqual(second_report.changed_user_ids, set())
        self.assertEqual(agent.source_path, 'agents/a-first.yaml')
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
        publish.assert_not_called()

    @patch('apps.bus.resources.publish_resource_update')
    def test_removed_files_disable_owner_agents_with_one_event(
        self,
        publish: MagicMock,
    ) -> None:
        """Disable missing agents, retain row count, and group owner publication."""
        path = self.write_agent(cron='0 * * * *')
        second_path = self.write_agent(identifier='other-helper', name='Other Helper')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        trigger = Trigger.objects.get(agent=agent, agent_config=agent.current_config)
        task_name = periodic_task_name(trigger.id)
        self.assertTrue(PeriodicTask.objects.get(name=task_name).enabled)
        path.unlink()
        second_path.unlink()
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.disabled, 2)
        self.assertEqual(report.disabled_user_ids, {self.user.pk})
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        publish.assert_called_once_with(self.user.pk, 'agents')
        agent.refresh_from_db()
        self.assertEqual(agent.status, AgentStatus.DISABLED)
        self.assertFalse(PeriodicTask.objects.get(name=task_name).enabled)

    @patch('apps.bus.resources.publish_resource_update')
    def test_readded_unchanged_file_reactivates_agent_and_schedule_beat(
        self,
        publish: MagicMock,
    ) -> None:
        """Re-enable beat when an unchanged file restores a disabled agent."""
        path = self.write_agent(cron='0 * * * *')
        unchanged_content = path.read_text(encoding='utf-8')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        trigger = Trigger.objects.get(agent=agent, agent_config=agent.current_config)
        task_name = periodic_task_name(trigger.id)
        path.unlink()
        sync_agents_dir()
        self.assertFalse(PeriodicTask.objects.get(name=task_name).enabled)
        path.write_text(unchanged_content, encoding='utf-8')
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 0)
        self.assertEqual(report.changed_user_ids, {self.user.pk})
        self.assertEqual(report.items[0].user_id, self.user.pk)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'agents')
        agent.refresh_from_db()
        self.assertEqual(agent.status, AgentStatus.ACTIVE)
        self.assertTrue(PeriodicTask.objects.get(name=task_name).enabled)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)

    @patch('apps.bus.resources.publish_resource_update')
    def test_profile_only_change_publishes_without_new_revision(
        self,
        publish: MagicMock,
    ) -> None:
        """Publish a disk profile mutation when config bytes stay unchanged."""
        path = self.write_agent(name='Before')
        with self.captureOnCommitCallbacks(execute=True):
            sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        config_id = agent.current_config_id
        path.write_text(path.read_text(encoding='utf-8').replace('name: Before', 'name: After'), encoding='utf-8')
        publish.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            report = sync_agents_dir()

        agent.refresh_from_db()
        self.assertEqual(agent.name, 'After')
        self.assertEqual(agent.current_config_id, config_id)
        self.assertTrue(report.items[0].changed)
        publish.assert_called_once_with(self.user.pk, 'agents')

    def test_bad_yaml_keeps_last_good_config_active(self) -> None:
        """Keep the last valid revision active after malformed disk content."""
        path = self.write_agent(prompt='Last good prompt.')
        sync_agents_dir()
        agent = Agent.objects.get(user=self.user, identifier='daily-helper')
        old_config_id = agent.current_config_id
        path.write_text('owner: alice\nllm: [not valid\n', encoding='utf-8')

        with self.assertLogs('apps.agents.services.disk_sync', level='ERROR'):
            report = sync_agents_dir()

        self.assertEqual(report.failed, 1)
        agent.refresh_from_db()
        self.assertEqual(agent.status, AgentStatus.ACTIVE)
        self.assertEqual(agent.current_config_id, old_config_id)
        self.assertEqual(AgentConfig.objects.filter(agent=agent).count(), 1)
