# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify Docker Compose and container entrypoint conventions."""

import re
from pathlib import Path

from ruamel.yaml import YAML

from olib.py.django.test.cases import OTestCase


class TestComposeLocalProviderConfig(OTestCase):
    """Check that Compose uses one fixed local-provider directory."""

    def test_compose_uses_fixed_local_provider_directory(self) -> None:
        """All consumers mount .local without user-configurable assignment lines."""
        repository_root = Path(__file__).resolve().parents[3]
        compose_path = repository_root / 'infra/docker/docker-compose.yml'
        compose_source = compose_path.read_text()
        compose = YAML(typ='safe').load(compose_source)

        for service_name in ('chief-backend', 'chief-worker', 'chief-beat'):
            service = compose['services'][service_name]
            local_target_mounts = []
            for mount in service['volumes']:
                volume_parts = mount.split(':', maxsplit=2)
                target = volume_parts[0] if len(volume_parts) == 1 else volume_parts[1]
                if target == '/mnt/local':
                    local_target_mounts.append(mount)
            self.assertEqual(local_target_mounts, ['../../.local:/mnt/local'])
            self.assertEqual(
                service['environment']['CHIEF_LOCAL_DIR'],
                '/mnt/local',
            )

        for legacy_compose_value in (
            'CHIEF_AGENTS_DIR',
            'CHIEF_KEYS_DIR',
            '../../.local/agents',
            '../../.local/keys',
        ):
            self.assertNotIn(legacy_compose_value, compose_source)

        env_example = (repository_root / '.env.local.example').read_text()
        for setting_name in (
            'CHIEF_LOCAL_DIR',
            'CHIEF_AGENTS_DIR',
            'CHIEF_KEYS_DIR',
        ):
            self.assertIsNone(
                re.search(rf'^\s*{re.escape(setting_name)}\s*=', env_example, flags=re.MULTILINE),
            )


class TestCeleryEntrypointLogging(OTestCase):
    """Check terminal logging thresholds for Compose Celery processes."""

    def test_celery_processes_suppress_info_logging(self) -> None:
        """Run worker and beat at WARNING so routine INFO records stay hidden."""
        repository_root = Path(__file__).resolve().parents[3]
        entrypoint_source = (repository_root / 'backend/entrypoint.sh').read_text()

        self.assertIn('celery -A chief worker --loglevel=WARNING', entrypoint_source)
        self.assertIn('celery -A chief beat --loglevel=WARNING', entrypoint_source)
        self.assertNotIn('--loglevel=INFO', entrypoint_source)
