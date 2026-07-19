# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify Docker Compose and container entrypoint conventions."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from ruamel.yaml import YAML

from olib.py.django.test.cases import OTestCase

_PYTHON_IMAGE = 'python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91'
_UV_IMAGE = 'ghcr.io/astral-sh/uv:0.11.29@' + 'sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc'
_NGINX_IMAGE = 'nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de'


def _run_container_entrypoint(*, entrypoint: str, debug: str | None) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the real entrypoint against recording stand-ins for container commands."""
    repository_root = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory() as temporary_directory:
        runtime_root = Path(temporary_directory)
        shutil.copy2(repository_root / 'backend/entrypoint.sh', runtime_root / 'entrypoint.sh')
        command_log = runtime_root / 'commands.log'
        recorder_source = '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$COMMAND_LOG"\n'
        for command_name in ('manage.py', 'uvicorn', 'celery'):
            command_path = runtime_root / command_name
            command_path.write_text(recorder_source)
            command_path.chmod(0o755)

        environment = os.environ.copy()
        environment['COMMAND_LOG'] = str(command_log)
        environment['ENTRYPOINT'] = entrypoint
        environment['PATH'] = f'{runtime_root}:{environment["PATH"]}'
        if debug is None:
            environment.pop('DEBUG', None)
        else:
            environment['DEBUG'] = debug

        result = subprocess.run(
            ['/bin/bash', str(runtime_root / 'entrypoint.sh')],
            cwd=runtime_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        commands = command_log.read_text() if command_log.exists() else ''
        return result, commands


def _run_isolated_settings_script(script: str, child_env: dict[str, str], repository_root: Path) -> list[object]:
    """Run a settings-import script in a subprocess isolated from dotenv/ambient env vars."""
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
        return _run_isolated_settings_script(script, child_env, repository_root)

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


class TestDropboxOAuthApplicationConfig(OTestCase):
    """Check optional Dropbox OAuth app settings and their deployment contract."""

    @staticmethod
    def _load_oauth_settings(app_key: str | None, app_secret: str | None) -> list[object]:
        """Import project settings in isolation from dotenv and ambient Dropbox OAuth values."""
        repository_root = Path(__file__).resolve().parents[3]
        child_env = os.environ.copy()
        for setting_name in (
            'DROPBOX_OAUTH_APP_KEY',
            'DROPBOX_OAUTH_APP_KEY_FILE',
            'DROPBOX_OAUTH_APP_SECRET',
            'DROPBOX_OAUTH_APP_SECRET_FILE',
        ):
            child_env.pop(setting_name, None)
        if app_key is not None:
            child_env['DROPBOX_OAUTH_APP_KEY'] = app_key
        if app_secret is not None:
            child_env['DROPBOX_OAUTH_APP_SECRET'] = app_secret

        script = (
            'import json, sys\n'
            "sys.argv = ['manage.py', 'test']\n"
            'from chief import settings\n'
            'print(json.dumps(['
            'settings.DROPBOX_OAUTH_APP_KEY, '
            'settings.DROPBOX_OAUTH_APP_SECRET]))\n'
        )
        return _run_isolated_settings_script(script, child_env, repository_root)

    def test_blank_settings_allow_startup_without_dropbox_oauth(self) -> None:
        """OAuth app credentials are optional so deployments without Dropbox OAuth still start."""
        self.assertEqual(self._load_oauth_settings(None, None), ['', ''])

    def test_oauth_settings_propagate_configured_values(self) -> None:
        """Project settings preserve deployment-provided Dropbox OAuth application values."""
        self.assertEqual(
            self._load_oauth_settings('app-key-sentinel', 'app-secret-sentinel'),
            ['app-key-sentinel', 'app-secret-sentinel'],
        )

    def test_env_example_places_blank_oauth_values_in_backend_group(self) -> None:
        """Compose exposes only blank Dropbox OAuth app placeholders to backend consumers."""
        repository_root = Path(__file__).resolve().parents[3]
        env_example = (repository_root / '.env.local.example').read_text()
        backend_match = re.search(
            r'^#\[backend\]\s*$\n(?P<body>.*?)(?=^#\[|\Z)',
            env_example,
            flags=re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(backend_match)
        backend_body = backend_match.group('body') if backend_match else ''
        for setting_name in ('DROPBOX_OAUTH_APP_KEY', 'DROPBOX_OAUTH_APP_SECRET'):
            assignment = f'{setting_name}='
            self.assertEqual(env_example.count(assignment), 1)
            self.assertRegex(backend_body, rf'(?m)^{re.escape(assignment)}$')

    def test_architecture_documents_dropbox_oauth_knox_and_callback_contract(self) -> None:
        """Architecture fixes the Dropbox Knox mapping and callback beside Google's."""
        repository_root = Path(__file__).resolve().parents[3]
        architecture = (repository_root / 'docs/ARCHITECTURE.md').read_text()

        self.assertIn('`$KNOX/chief/oauth/dropbox`', architecture)
        self.assertIn('- `app_key` → `DROPBOX_OAUTH_APP_KEY`', architecture)
        self.assertIn('- `app_secret` → `DROPBOX_OAUTH_APP_SECRET`', architecture)
        self.assertIn('`https://<origin>/settings/keys/oauth/dropbox/callback/`', architecture)


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


class TestCeleryWorkerPrivileges(OTestCase):
    """Check that Celery never needs its root-safety bypass."""

    def test_compose_worker_uses_registered_host_identity(self) -> None:
        """Map the host ids onto a named image user so Celery can resolve them."""
        repository_root = Path(__file__).resolve().parents[3]
        compose_path = repository_root / 'infra/docker/docker-compose.yml'
        compose = YAML(typ='safe').load(compose_path.read_text())
        dockerfile_source = (repository_root / 'backend/Dockerfile.dev').read_text()
        entrypoint_source = (repository_root / 'backend/entrypoint.sh').read_text()
        worker = compose['services']['chief-worker']
        expected_build_args = {'APP_UID': '${UID:-1000}', 'APP_GID': '${GID:-1000}'}

        self.assertEqual(worker['user'], 'app')
        for service_name in ('chief-backend', 'chief-worker', 'chief-beat'):
            self.assertEqual(compose['services'][service_name]['build']['args'], expected_build_args)
        self.assertIn('ARG APP_UID=1000', dockerfile_source)
        self.assertIn('ARG APP_GID=1000', dockerfile_source)
        self.assertIn('test "$APP_UID" -gt 0', dockerfile_source)
        self.assertIn('test "$APP_GID" -gt 0', dockerfile_source)
        self.assertIn('groupadd --non-unique --gid "$APP_GID" app', dockerfile_source)
        self.assertIn('useradd --non-unique --uid "$APP_UID" --gid app', dockerfile_source)
        self.assertNotIn('C_FORCE_ROOT', entrypoint_source)


class TestProductionContainerConfig(OTestCase):
    """Verify hosted images and processes avoid development-only behavior."""

    def test_debug_web_keeps_compose_bootstrap_and_reload(self) -> None:
        """The default debug path retains migrations, admin bootstrap, and reload."""
        result, commands = _run_container_entrypoint(entrypoint='web-server', debug=None)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('manage.py migrate --noinput', commands)
        self.assertIn('manage.py ensure_superuser --no-input', commands)
        self.assertIn('uvicorn chief.asgi:application', commands)
        self.assertIn('--reload', commands)

    def test_production_web_skips_bootstrap_and_uses_fixed_workers(self) -> None:
        """Hosted web starts only a fixed-worker uvicorn process without reload."""
        result, commands = _run_container_entrypoint(entrypoint='web-server', debug='false')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('manage.py', commands)
        self.assertIn('uvicorn chief.asgi:application', commands)
        self.assertIn('--host 0.0.0.0 --port 8000', commands)
        self.assertIn('--workers 4', commands)
        self.assertNotIn('--reload', commands)

    def test_worker_and_beat_keep_operational_flags(self) -> None:
        """Celery process selections preserve log level and worker thread settings."""
        worker_result, worker_commands = _run_container_entrypoint(entrypoint='celery-worker', debug='false')
        beat_result, beat_commands = _run_container_entrypoint(entrypoint='celery-beat', debug='false')

        self.assertEqual(worker_result.returncode, 0, worker_result.stderr)
        self.assertIn(
            'celery -A chief worker --loglevel=WARNING --pool=threads --concurrency=16',
            worker_commands,
        )
        self.assertEqual(beat_result.returncode, 0, beat_result.stderr)
        self.assertIn('celery -A chief beat --loglevel=WARNING', beat_commands)

    def test_invalid_entrypoint_selection_exits_nonzero(self) -> None:
        """Unknown process selections fail instead of starting an unintended service."""
        result, commands = _run_container_entrypoint(entrypoint='unknown', debug='false')

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, '')
        self.assertIn('invalid entrypoint selection', result.stdout)

    def test_deployment_images_pin_single_stage_runtime_inputs(self) -> None:
        """Deployment images use exact manifests without duplicate runtime stages."""
        repository_root = Path(__file__).resolve().parents[3]
        backend_source = (repository_root / 'backend/Dockerfile.prod').read_text()
        static_source = (repository_root / 'infra/k8s/Dockerfile.static.stage').read_text()
        backend_from_images = re.findall(r'^FROM (\S+)', backend_source, flags=re.MULTILINE)
        static_from_images = re.findall(r'^FROM (\S+)', static_source, flags=re.MULTILINE)
        backend_uv_images = re.findall(r'^COPY --from=(\S+) /uv ', backend_source, flags=re.MULTILINE)

        self.assertEqual(backend_from_images, [_PYTHON_IMAGE])
        self.assertEqual(static_from_images, [_NGINX_IMAGE])
        self.assertEqual(backend_uv_images, [_UV_IMAGE])
        self.assertNotIn('ghcr.io/astral-sh/uv', static_source)

    def test_backend_image_uses_only_backend_dependency_metadata_as_non_root(self) -> None:
        """The production backend installs its standalone locked environment for app."""
        repository_root = Path(__file__).resolve().parents[3]
        source = (repository_root / 'backend/Dockerfile.prod').read_text()

        self.assertNotIn(' AS builder', source)
        self.assertNotIn(' AS runtime', source)
        self.assertIn('COPY ./backend/pyproject.toml /app/pyproject.toml', source)
        self.assertIn('COPY ./backend/uv.lock /app/uv.lock', source)
        self.assertNotIn('COPY ./pyproject.toml', source)
        self.assertNotIn('COPY ./infra/pyproject.toml', source)
        self.assertNotIn('COPY ./olib/pyproject.toml', source)
        self.assertIn('uv export --frozen --package chief-backend-env --output-file requirements.lock.txt', source)
        self.assertIn('uv pip install --system --require-hashes', source)
        self.assertNotIn('--no-hashes', source)
        self.assertIn('rm -f requirements.lock.txt /usr/local/bin/uv', source)
        self.assertIn('COPY --chown=app:app ./olib', source)
        self.assertIn('COPY --chown=app:app ./backend', source)
        self.assertIn('WORKDIR /app', source)
        self.assertIn('USER app', source)
        self.assertIn('EXPOSE 8000', source)
        self.assertIn('CMD ["./entrypoint.sh"]', source)
        self.assertIn('rm -rf /var/lib/apt/lists/*', source)
        self.assertTrue((repository_root / 'backend/uv.lock').is_file())

    def test_static_image_packages_config_generated_assets_only(self) -> None:
        """The nginx image packages host-generated assets without Python tooling."""
        repository_root = Path(__file__).resolve().parents[3]
        source = (repository_root / 'infra/k8s/Dockerfile.static.stage').read_text()

        self.assertNotIn('python:', source)
        self.assertNotIn('manage.py', source)
        self.assertNotIn('collectstatic', source)
        self.assertIn('backend/.output/static /etc/storage/public/static', source)
        self.assertIn('infra/k8s/nginx.static.conf /etc/nginx/nginx.conf', source)
        self.assertIn('/tmp/client_temp', source)
        self.assertIn('USER nginx', source)

        nginx_source = (repository_root / 'infra/k8s/nginx.static.conf').read_text()
        self.assertIn('include /etc/nginx/mime.types;', nginx_source)
        self.assertNotIn('application/javascript js mjs;', nginx_source)

    def test_static_build_context_includes_only_generated_assets(self) -> None:
        """Docker includes collected static files while other output stays ignored."""
        repository_root = Path(__file__).resolve().parents[3]
        dockerignore = (repository_root / '.dockerignore').read_text()

        self.assertIn('\n.output\n', dockerignore)
        self.assertIn('!backend/.output/static\n', dockerignore)
        self.assertIn('!backend/.output/static/**\n', dockerignore)

        backend_dockerignore_path = repository_root / 'backend/Dockerfile.prod.dockerignore'
        self.assertTrue(backend_dockerignore_path.exists())
        backend_dockerignore = backend_dockerignore_path.read_text()
        self.assertIn('\n.output\n', backend_dockerignore)
        self.assertNotIn('!backend/.output/static', backend_dockerignore)
