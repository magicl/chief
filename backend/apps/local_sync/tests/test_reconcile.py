# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for finite local-provider reconciliation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from apps.local_sync.reconcile import (
    LocalSyncReport,
    reconcile_local_providers,
    resolve_local_root,
)
from django.test import override_settings
from libs.file.sync import SyncReport

from olib.py.django.test.cases import OTestCase


class TestResolveLocalRoot(OTestCase):
    @override_settings(CHIEF_LOCAL_DIR='')
    def test_unset_root_is_inactive(self) -> None:
        """Return no root when local providers are not configured."""
        self.assertIsNone(resolve_local_root())

    def test_configured_root_is_resolved_without_creating_it(self) -> None:
        """Resolve a configured missing root without creating operator paths."""
        with TemporaryDirectory() as parent:
            root = Path(parent) / 'missing' / '..' / 'local'
            expected = root.resolve()
            with override_settings(CHIEF_LOCAL_DIR=str(root)):
                resolved = resolve_local_root()

        self.assertEqual(resolved, expected)
        self.assertFalse(expected.exists())


class TestReconcileLocalProviders(OTestCase):
    def test_reconcile_runs_keys_before_agents_once(self) -> None:
        """Synchronize credentials before agent configs exactly once."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            key_report = SyncReport()
            agent_report = SyncReport()
            order: list[str] = []

            def record_progress() -> None:
                """Provide one generic maintenance callback to both domains."""

            def sync_keys_once(*, root: Path, progress: object) -> SyncReport:
                """Record one key reconciliation and return its report."""
                order.append('keys')
                self.assertIs(progress, record_progress)
                return key_report

            def sync_agents_once(*, root: Path, progress: object) -> SyncReport:
                """Record one agent reconciliation and return its report."""
                order.append('agents')
                self.assertIs(progress, record_progress)
                return agent_report

            with (
                patch(
                    'apps.local_sync.reconcile.sync_keys_dir',
                    side_effect=sync_keys_once,
                ) as sync_keys,
                patch(
                    'apps.local_sync.reconcile.sync_agents_dir',
                    side_effect=sync_agents_once,
                ) as sync_agents,
            ):
                report = reconcile_local_providers(root=root, progress=record_progress)

        self.assertEqual(order, ['keys', 'agents'])
        self.assertEqual(report, LocalSyncReport(keys=key_report, agents=agent_report))
        self.assertEqual(sync_keys.call_args_list, [call(root=root, progress=record_progress)])
        self.assertEqual(sync_agents.call_args_list, [call(root=root, progress=record_progress)])

    def test_reconcile_does_not_create_provider_directories(self) -> None:
        """Leave operator-owned keys and agents directories untouched."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch('apps.local_sync.reconcile.sync_keys_dir', return_value=SyncReport()),
                patch('apps.local_sync.reconcile.sync_agents_dir', return_value=SyncReport()),
            ):
                reconcile_local_providers(root=root)

            self.assertFalse((root / 'keys').exists())
            self.assertFalse((root / 'agents').exists())
