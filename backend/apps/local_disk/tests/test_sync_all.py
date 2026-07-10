# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.local_disk.key_sync import SyncItemResult, SyncReport
from apps.local_disk.sync import sync_all
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class TestSyncAll(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='')
    def test_unset_root_is_inactive(self) -> None:
        """Leave both providers untouched when no local root is configured."""
        with (
            patch('apps.local_disk.sync.sync_keys_dir') as sync_keys,
            patch('apps.local_disk.sync.sync_agents_dir') as sync_agents,
        ):
            report = sync_all()

        self.assertEqual(report, SyncReport())
        sync_keys.assert_not_called()
        sync_agents.assert_not_called()

    def test_missing_root_is_inactive(self) -> None:
        """Leave both providers untouched when the configured root is absent."""
        with TemporaryDirectory() as parent:
            missing = Path(parent) / 'missing'
            with (
                override_settings(CHIEF_LOCAL_DIR=str(missing)),
                patch('apps.local_disk.sync.sync_keys_dir') as sync_keys,
                patch('apps.local_disk.sync.sync_agents_dir') as sync_agents,
            ):
                report = sync_all()

        self.assertEqual(report, SyncReport())
        sync_keys.assert_not_called()
        sync_agents.assert_not_called()

    def test_runs_keys_before_agents_and_combines_reports(self) -> None:
        """Synchronize keys first and return one combined provider report."""
        calls: list[str] = []
        key_report = SyncReport(items=[SyncItemResult('keys/key.yaml', True)], disabled=1)
        agent_report = SyncReport(items=[SyncItemResult('agents/agent.yaml', True)], disabled=2)

        def sync_keys() -> SyncReport:
            """Record key provider ordering and return its sample report."""
            calls.append('keys')
            return key_report

        def sync_agents() -> SyncReport:
            """Record agent provider ordering and return its sample report."""
            calls.append('agents')
            return agent_report

        with TemporaryDirectory() as root:
            with (
                override_settings(CHIEF_LOCAL_DIR=root),
                patch('apps.local_disk.sync.sync_keys_dir', side_effect=sync_keys),
                patch('apps.local_disk.sync.sync_agents_dir', side_effect=sync_agents),
            ):
                report = sync_all()

            self.assertTrue((Path(root) / 'keys').is_dir())
            self.assertTrue((Path(root) / 'agents').is_dir())

        self.assertEqual(calls, ['keys', 'agents'])
        self.assertEqual(report.items, key_report.items + agent_report.items)
        self.assertEqual(report.disabled, 3)
