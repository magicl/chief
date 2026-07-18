# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for agent create/rename command helpers."""

from unittest.mock import MagicMock, patch

from apps.agents.models import Agent, AgentConfig
from apps.agents.services import config_commands
from apps.agents.services.config_commands import (
    ConfigCommandError,
    create_from_yaml,
    normalize_identifier,
    suggest_identifier,
    update_agent_profile,
)
from django.contrib.auth import get_user_model
from libs.agent_spec import load_example
from libs.agent_spec.yaml_dump import dump_agent_config_spec

from olib.py.django.test.cases import OTestCase


class AgentIdentityTests(OTestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username='identity-user', password='x')

    def test_normalize_identifier_from_display_name(self) -> None:
        self.assertEqual(normalize_identifier('Bob agent'), 'bob-agent')

    def test_suggest_identifier_adds_numeric_suffix(self) -> None:
        Agent.objects.create(user=self.user, name='Clock', identifier='clock')
        self.assertEqual(suggest_identifier(self.user.pk, 'clock'), 'clock-2')

    def test_create_from_yaml_requires_name(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('minimal'))
        with self.assertRaises(ConfigCommandError):
            create_from_yaml(self.user, spec_yaml, name='')

    def test_create_from_yaml_derives_identifier_from_name(self) -> None:
        spec_yaml = dump_agent_config_spec(load_example('minimal'))
        agent = create_from_yaml(self.user, spec_yaml, name='Bob agent')
        self.assertEqual(agent.name, 'Bob agent')
        self.assertEqual(agent.identifier, 'bob-agent')

    @patch('apps.bus.resources.publish_resource_update')
    def test_create_from_yaml_publishes_once(self, publish: MagicMock) -> None:
        """Publish one create hint through nested config persistence."""
        spec_yaml = dump_agent_config_spec(load_example('minimal'))

        with self.captureOnCommitCallbacks(execute=True):
            create_from_yaml(self.user, spec_yaml, name='Published YAML agent')

        publish.assert_called_once_with(self.user.pk, 'agents')

    @patch('apps.bus.resources.publish_resource_update')
    def test_profile_change_reports_mutation_and_publishes(self, publish: MagicMock) -> None:
        """Return true and notify once for a visible profile change."""
        agent = Agent.objects.create(user=self.user, name='Before', identifier='before')

        with self.captureOnCommitCallbacks(execute=True):
            changed = update_agent_profile(agent, self.user.pk, name='After', identifier='after')

        self.assertTrue(changed)
        publish.assert_called_once_with(self.user.pk, 'agents')

    @patch('apps.bus.resources.publish_resource_update')
    def test_identical_profile_reports_no_mutation(self, publish: MagicMock) -> None:
        """Return false and suppress hints for identical profile values."""
        agent = Agent.objects.create(user=self.user, name='Same', identifier='same')

        with self.captureOnCommitCallbacks(execute=True):
            changed = update_agent_profile(agent, self.user.pk, name='Same', identifier='same')

        self.assertFalse(changed)
        publish.assert_not_called()

    @patch('apps.bus.resources.publish_resource_update')
    def test_profile_publish_can_be_coalesced_with_config_save(self, publish: MagicMock) -> None:
        """Allow combined saves to rely on the subsequent config event."""
        agent = Agent.objects.create(user=self.user, name='Before', identifier='before')

        with self.captureOnCommitCallbacks(execute=True):
            changed = update_agent_profile(
                agent,
                self.user.pk,
                name='After',
                publish_update=False,
            )

        self.assertTrue(changed)
        publish.assert_not_called()

    @patch('apps.bus.resources.publish_resource_update')
    def test_combined_profile_config_save_publishes_once(self, publish: MagicMock) -> None:
        """Commit profile and config together with one refresh hint."""
        agent = Agent.objects.create(user=self.user, name='Before', identifier='before')
        command = getattr(config_commands, 'save_agent_profile_and_config', None)
        self.assertIsNotNone(command)
        assert command is not None

        with self.captureOnCommitCallbacks(execute=True):
            config = command(
                agent,
                self.user.pk,
                load_example('minimal'),
                name='After',
                identifier='after',
                source_rev='ui:combined',
            )

        agent.refresh_from_db()
        self.assertEqual(agent.name, 'After')
        self.assertEqual(agent.identifier, 'after')
        self.assertEqual(agent.current_config_id, config.pk)
        publish.assert_called_once_with(self.user.pk, 'agents')

    @patch('apps.bus.resources.publish_resource_update')
    @patch('apps.agents.ingest.materialize_agent_config')
    def test_combined_save_rolls_back_profile_when_materialization_fails(
        self,
        materialize: MagicMock,
        publish: MagicMock,
    ) -> None:
        """Rollback profile/config writes and suppress events after persist failure."""
        agent = Agent.objects.create(user=self.user, name='Before', identifier='before')
        materialize.side_effect = RuntimeError('materialization failed')
        command = getattr(config_commands, 'save_agent_profile_and_config', None)
        self.assertIsNotNone(command)
        assert command is not None

        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=True):
                command(
                    agent,
                    self.user.pk,
                    load_example('minimal'),
                    name='After',
                    identifier='after',
                    source_rev='ui:combined',
                )

        agent.refresh_from_db()
        self.assertEqual(agent.name, 'Before')
        self.assertEqual(agent.identifier, 'before')
        self.assertFalse(AgentConfig.objects.filter(agent=agent).exists())
        publish.assert_not_called()
