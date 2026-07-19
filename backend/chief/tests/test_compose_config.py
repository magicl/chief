# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify Docker Compose and container entrypoint conventions."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from ruamel.yaml import YAML

from olib.py.django.test.cases import OTestCase


class TestGoogleOAuthApplicationConfig(OTestCase):
    """Check optional Google OAuth app settings and their deployment contract."""

    @staticmethod
    def _load_oauth_settings(client_id: str | None, client_secret: str | None) -> list[object]:
        """Import project settings in isolation from dotenv and ambient OAuth values."""
        repository_root = Path(__file__).resolve().parents[3]
        child_env = os.environ.copy()
        for setting_name in (
            'GOOGLE_OAUTH_CLIENT_ID',
            'GOOGLE_OAUTH_CLIENT_ID_FILE',
            'GOOGLE_OAUTH_CLIENT_SECRET',
            'GOOGLE_OAUTH_CLIENT_SECRET_FILE',
        ):
            child_env.pop(setting_name, None)
        if client_id is not None:
            child_env['GOOGLE_OAUTH_CLIENT_ID'] = client_id
        if client_secret is not None:
            child_env['GOOGLE_OAUTH_CLIENT_SECRET'] = client_secret

        script = (
            'import json, sys\n'
            "sys.argv = ['manage.py', 'test']\n"
            'from chief import settings\n'
            'print(json.dumps(['
            'settings.GOOGLE_OAUTH_CLIENT_ID, '
            'settings.GOOGLE_OAUTH_CLIENT_SECRET, '
            'settings.OAUTH_STATE_MAX_AGE_SECONDS]))\n'
        )
        with tempfile.TemporaryDirectory() as empty_env_dir:
            child_env['ENV_PATH'] = empty_env_dir
            completed = subprocess.run(  # noqa: S603
                [sys.executable, '-c', script],
                cwd=repository_root / 'backend',
                env=child_env,
                check=False,
                capture_output=True,
                text=True,
            )
        if completed.returncode:
            raise AssertionError(f'isolated settings import failed: {completed.stderr}')
        return cast('list[object]', json.loads(completed.stdout))

    def test_blank_settings_allow_startup_without_google_oauth(self) -> None:
        """OAuth app credentials are optional while state lifetime stays bounded."""
        self.assertEqual(self._load_oauth_settings(None, None), ['', '', 600])

    def test_oauth_settings_propagate_configured_values(self) -> None:
        """Project settings preserve deployment-provided OAuth application values."""
        self.assertEqual(
            self._load_oauth_settings('client-id-sentinel', 'client-secret-sentinel'),
            ['client-id-sentinel', 'client-secret-sentinel', 600],
        )

    def test_env_example_places_blank_oauth_values_in_backend_group(self) -> None:
        """Compose exposes only blank OAuth app placeholders to backend consumers."""
        repository_root = Path(__file__).resolve().parents[3]
        env_example = (repository_root / '.env.local.example').read_text()
        backend_match = re.search(
            r'^#\[backend\]\s*$\n(?P<body>.*?)(?=^#\[|\Z)',
            env_example,
            flags=re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(backend_match)
        backend_body = backend_match.group('body') if backend_match else ''
        for setting_name in ('GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET'):
            assignment = f'{setting_name}='
            self.assertEqual(env_example.count(assignment), 1)
            self.assertRegex(backend_body, rf'(?m)^{re.escape(assignment)}$')

    def test_architecture_documents_oauth_secret_boundaries(self) -> None:
        """Architecture fixes Knox mapping, callback, and credential ownership rules."""
        repository_root = Path(__file__).resolve().parents[3]
        architecture = (repository_root / 'docs/ARCHITECTURE.md').read_text()

        self.assertEqual(architecture.count('`$KNOX/chief/oauth/google`'), 1)
        self.assertIn('- `client_id` → `GOOGLE_OAUTH_CLIENT_ID`', architecture)
        self.assertIn('- `client_secret` → `GOOGLE_OAUTH_CLIENT_SECRET`', architecture)
        self.assertIn('Chief never reads Knox directly', architecture)
        self.assertIn('`https://<origin>/settings/keys/oauth/google/callback/`', architecture)
        self.assertIn('HTTPS outside local development', architecture)

    def test_architecture_documents_oauth_proxy_and_process_boundaries(self) -> None:
        """Architecture states the exact production proxy and process constraints."""
        repository_root = Path(__file__).resolve().parents[3]
        architecture = (repository_root / 'docs/ARCHITECTURE.md').read_text()
        normalized = ' '.join(architecture.split())

        self.assertIn(
            'production Django application port must be network-isolated and unreachable directly',
            normalized,
        )
        self.assertIn('trusted front proxy must overwrite `X-Forwarded-Proto`', normalized)
        self.assertIn(
            'current Docker Compose port publishing is local development only and is not a production exposure template',
            normalized,
        )
        self.assertIn('`.env.local` into the backend, worker, and Beat services', normalized)
        self.assertIn('Beat does not use the Google OAuth values', normalized)
        self.assertIn('Production secret scoping is the deployment responsibility', normalized)

    def test_architecture_documents_callback_logging_contract(self) -> None:
        """Require query-free upstream logging and an isolated proxy boundary."""
        repository_root = Path(__file__).resolve().parents[3]
        architecture = (repository_root / 'docs/ARCHITECTURE.md').read_text()
        normalized = ' '.join(architecture.split())
        nginx = (repository_root / 'infra/docker/nginx.conf').read_text()

        self.assertIn(
            'Production ingress, access logs, and APM must omit the OAuth callback query string entirely before '
            'the request reaches Django.',
            normalized,
        )
        self.assertIn(
            'Application middleware cannot redact logs already emitted by upstream infrastructure.',
            normalized,
        )
        self.assertIn(
            'The backend must remain network-isolated behind that controlled proxy.',
            normalized,
        )
        self.assertIn('The current development nginx keeps `access_log off`.', normalized)
        self.assertRegex(nginx, r'(?m)^\s*access_log off;\s*$')


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


class TestComposeRichContentAssets(OTestCase):
    """Check that static Nginx serves the external generated renderer lane."""

    def test_package_install_provisions_chromium(self) -> None:
        """JavaScript initialization must install the browser required by its test gate."""
        repository_root = Path(__file__).resolve().parents[3]
        package_path = repository_root / 'backend/apps/web/static/web/package.json'
        package = json.loads(package_path.read_text())

        self.assertEqual(package['scripts']['postinstall'], 'pnpm run setup:browser')

    def test_static_service_mounts_generated_renderer_read_only(self) -> None:
        """The rich-content target has exactly one read-only external-assets mount."""
        repository_root = Path(__file__).resolve().parents[3]
        compose_path = repository_root / 'infra/docker/docker-compose.yml'
        compose = YAML(typ='safe').load(compose_path.read_text())
        rich_content_target = '/etc/storage/public/static/web/rich-content'
        rich_content_mounts = []
        for mount in compose['services']['chief-static']['volumes']:
            volume_parts = mount.split(':', maxsplit=2)
            if len(volume_parts) >= 2 and volume_parts[1] == rich_content_target:
                rich_content_mounts.append(mount)

        self.assertEqual(
            rich_content_mounts,
            ['/mnt/infra-assets/chief/js/gen:/etc/storage/public/static/web/rich-content:ro'],
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
