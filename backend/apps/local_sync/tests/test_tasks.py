# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for leased local-provider Celery reconciliation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch
from uuid import UUID

from apps.local_sync.tasks import reconcile_local_providers_task
from libs.file.sync import SyncProgressCallback, SyncReport

from olib.py.django.test.cases import OTestCase


class TestReconcileLocalProvidersTask(OTestCase):
    def test_task_contract_is_explicit_and_resultless(self) -> None:
        """Expose the exact Beat task name without storing task results."""
        self.assertEqual(reconcile_local_providers_task.name, 'apps.local_sync.tasks.reconcile_local_providers')
        self.assertTrue(reconcile_local_providers_task.ignore_result)

    @patch('apps.local_sync.tasks.try_acquire_lease')
    @patch('apps.local_sync.tasks.resolve_local_root', return_value=None)
    def test_unset_root_returns_before_lease(self, _resolve: MagicMock, acquire: MagicMock) -> None:
        """Avoid Redis work when no local root is configured."""
        reconcile_local_providers_task()

        acquire.assert_not_called()

    @patch('apps.local_sync.tasks.reconcile_local_providers')
    @patch('apps.local_sync.tasks.try_acquire_lease')
    def test_missing_root_returns_before_lease(self, acquire: MagicMock, reconcile: MagicMock) -> None:
        """Avoid Redis and reconciliation when the configured root is missing."""
        with TemporaryDirectory() as parent:
            missing = Path(parent) / 'missing'
            with patch('apps.local_sync.tasks.resolve_local_root', return_value=missing):
                reconcile_local_providers_task()

        acquire.assert_not_called()
        reconcile.assert_not_called()

    @patch('apps.local_sync.tasks.release_lease')
    @patch('apps.local_sync.tasks.reconcile_local_providers')
    @patch('apps.local_sync.tasks.try_acquire_lease', return_value=False)
    def test_unavailable_lease_skips_reconciliation(
        self,
        acquire: MagicMock,
        reconcile: MagicMock,
        release: MagicMock,
    ) -> None:
        """Leave reconciliation to the current lease holder."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch('apps.local_sync.tasks.resolve_local_root', return_value=root):
                reconcile_local_providers_task()

        acquire.assert_called_once()
        reconcile.assert_not_called()
        release.assert_not_called()

    @patch('apps.local_sync.tasks.release_lease')
    @patch('apps.local_sync.tasks.renew_lease', return_value=True)
    @patch('apps.local_sync.tasks.try_acquire_lease', return_value=True)
    def test_acquired_lease_renews_at_synchronous_reconciliation_checkpoints(
        self,
        acquire: MagicMock,
        renew: MagicMock,
        release: MagicMock,
    ) -> None:
        """Renew the owned lease at finite checkpoints without background work."""
        owner = UUID('12345678-1234-5678-1234-567812345678')

        def reconcile_once(*, root: Path, progress: SyncProgressCallback) -> MagicMock:
            """Model two bounded progress checkpoints inside one reconciliation."""
            progress()
            progress()
            return MagicMock(keys=SyncReport(), agents=SyncReport())

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch('apps.local_sync.tasks.resolve_local_root', return_value=root),
                patch('apps.local_sync.tasks.uuid.uuid4', return_value=owner),
                patch(
                    'apps.local_sync.tasks.reconcile_local_providers',
                    side_effect=reconcile_once,
                ) as reconcile,
            ):
                reconcile_local_providers_task()

        acquire.assert_called_once_with('local-provider-sync', str(owner), ttl_seconds=30)
        reconcile.assert_called_once()
        self.assertEqual(
            renew.call_args_list,
            [
                call('local-provider-sync', str(owner), ttl_seconds=30),
                call('local-provider-sync', str(owner), ttl_seconds=30),
            ],
        )
        release.assert_called_once_with('local-provider-sync', str(owner))

    @patch('apps.local_sync.tasks.logger')
    @patch('apps.local_sync.tasks.release_lease')
    @patch('apps.local_sync.tasks.renew_lease', side_effect=RuntimeError('redis unavailable'))
    @patch('apps.local_sync.tasks.try_acquire_lease', return_value=True)
    def test_checkpoint_failure_reaches_task_boundary_and_releases(
        self,
        _acquire: MagicMock,
        renew: MagicMock,
        release: MagicMock,
        logger: MagicMock,
    ) -> None:
        """Contain callback failures only at the Celery task boundary."""

        def reconcile_once(*, root: Path, progress: SyncProgressCallback) -> MagicMock:
            """Invoke one callback from inside the finite reconciliation."""
            progress()
            return MagicMock(keys=SyncReport(), agents=SyncReport())

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch('apps.local_sync.tasks.resolve_local_root', return_value=root),
                patch(
                    'apps.local_sync.tasks.reconcile_local_providers',
                    side_effect=reconcile_once,
                ),
            ):
                reconcile_local_providers_task()

        renew.assert_called_once()
        logger.exception.assert_called_once_with('Local provider reconciliation failed')
        release.assert_called_once()

    @patch('apps.local_sync.tasks.logger')
    @patch('apps.local_sync.tasks.release_lease')
    @patch('apps.local_sync.tasks.reconcile_local_providers', side_effect=RuntimeError('scan failed'))
    @patch('apps.local_sync.tasks.try_acquire_lease', return_value=True)
    def test_reconciliation_failure_is_logged_and_released_without_retry(
        self,
        _acquire: MagicMock,
        reconcile: MagicMock,
        release: MagicMock,
        logger: MagicMock,
    ) -> None:
        """Contain one task-boundary failure and wait for the next Beat tick."""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch('apps.local_sync.tasks.resolve_local_root', return_value=root):
                reconcile_local_providers_task()

        reconcile.assert_called_once()
        logger.exception.assert_called_once_with('Local provider reconciliation failed')
        release.assert_called_once()
