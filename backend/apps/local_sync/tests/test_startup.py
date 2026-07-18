# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests proving Django app startup is inert."""

from __future__ import annotations

from importlib import import_module
from unittest.mock import patch

from apps.runner.apps import RunnerConfig
from apps.web.apps import WebConfig

from olib.py.django.test.cases import OTestCase


class TestLocalProviderStartup(OTestCase):
    def test_web_and_runner_configs_inherit_noop_ready(self) -> None:
        """Keep web and worker app declarations free of startup hooks."""
        self.assertNotIn('ready', WebConfig.__dict__)
        self.assertNotIn('ready', RunnerConfig.__dict__)

    def test_ready_invokes_no_provider_sync(self) -> None:
        """Perform no filesystem or ORM reconciliation during app startup."""
        web_config = WebConfig('apps.web', import_module('apps.web'))
        runner_config = RunnerConfig('apps.runner', import_module('apps.runner'))

        with (
            patch('apps.local_sync.reconcile.sync_keys_dir') as sync_keys,
            patch('apps.local_sync.reconcile.sync_agents_dir') as sync_agents,
        ):
            web_config.ready()
            runner_config.ready()

        sync_keys.assert_not_called()
        sync_agents.assert_not_called()
