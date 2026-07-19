# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify Chief's hosted deployment configuration contract."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from dotenv import dotenv_values

import config
from olib.py.cli.run.templates.docker import get_local_compose_entrypoint_url
from olib.py.cli.run.utils.envfiles import split_env_files
from olib.py.django.test.cases import OTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class TestStageDeploymentConfig(OTestCase):
    """Verify the dev-cluster build and environment contract."""

    def _generated_backend_values(self, target: str) -> dict[str, str]:
        """Generate and read the backend env exactly as env.split writes it."""
        env_files = []
        for env_file in config.Config.envs[target].env_files:
            optional = env_file.startswith('?')
            relative_path = env_file[1:] if optional else env_file
            absolute_path = REPOSITORY_ROOT / relative_path
            env_files.append(f'?{absolute_path}' if optional else str(absolute_path))

        with TemporaryDirectory() as output_dir:
            output_prefix = str(Path(output_dir) / f'env.{target}')
            with self.captureStdout():
                split_env_files(env_files, output_prefix)
            generated_path = Path(f'{output_prefix}.backend')
            return dict(line.split('=', 1) for line in generated_path.read_text(encoding='utf-8').splitlines())

    def test_stage_target_builds_expected_images(self) -> None:
        """Stage uses one backend image for every Python workload."""
        stage = cast(config.ClusterInfo, config.Config.clusters['chief-stage'])

        self.assertEqual(stage.release_name, 'chief-stage')
        self.assertEqual(stage.cluster, 'dev')
        self.assertEqual(stage.registry_url, 'registry.dev.oivindloe.com')
        self.assertEqual(stage.host, 'https://chief.dev.oivindloe.com')
        self.assertTrue(stage.default)
        self.assertEqual(stage.version_increments, ['debug'])
        self.assertFalse(stage.version_push_tag)
        self.assertEqual(stage.static_dir, 'backend/.output/static')
        self.assertEqual(stage.cdn, 'onas')
        self.assertEqual(
            stage.docker_images,
            {
                'backend': './backend/Dockerfile.prod',
                'static': './infra/k8s/Dockerfile.static.stage',
            },
        )

    def test_cdn_upload_creates_destination_before_sync(self) -> None:
        """The deployment graph creates the release's static directory before rsync."""
        config_source = (REPOSITORY_ROOT / 'config.py').read_text()
        ensure_call = "ensure_cdn_dir(cdn_info, release_name, 'public/static')"
        sync_call = "sync_dir_to_cdn(cdn_info, release_name, static_dir, 'public/static')"

        self.assertIn("name='cdn.upload::[target]'", config_source)
        self.assertIn("deps=['django.collectstatic::backend']", config_source)
        self.assertIn("rdeps=['k8s.deploy::[target]']", config_source)
        self.assertIn(ensure_call, config_source)
        self.assertIn(sync_call, config_source)
        self.assertLess(config_source.index(ensure_call), config_source.index(sync_call))
        self.assertNotIn('static-image-deps', config_source)

    def test_stage_uses_shared_development_cdn(self) -> None:
        """Stage uploads backend static files to the shared development NAS."""
        cdn = config.Config.cdns['onas']

        self.assertEqual(cdn.upload_user, 'oivind')
        self.assertEqual(cdn.base_path, '/volume1/infra')
        self.assertEqual(cdn.hostname, 'onas')

    def test_stage_environment_uses_production_layers(self) -> None:
        """Generated stage values combine shared production and stage settings."""
        stage = config.Config.envs['chief-stage']

        self.assertEqual(stage.release_name, 'chief-stage')
        self.assertEqual(
            stage.env_files,
            ['.env', '.env.production', '.env.production.stage'],
        )

    def test_stage_generated_backend_excludes_development_bootstrap(self) -> None:
        """Generated stage values omit local bootstrap credentials and storage."""
        backend = self._generated_backend_values('chief-stage')

        self.assertFalse(any(key.startswith('DJANGO_SUPERUSER_') for key in backend))
        self.assertNotIn('CHIEF_LOCAL_DIR', backend)

    def test_build_context_targets_chief_environment_repository(self) -> None:
        """Build output uses Chief's application path in the shared env repository."""
        build_context = config.Config.meta.build_context
        if build_context is None:
            raise AssertionError('Chief build context is not configured')

        self.assertEqual(build_context.app_category, 'apps')
        self.assertEqual(build_context.app_name, 'chief')
        self.assertEqual(build_context.env_repo_path, Path('~/yolo/env').expanduser())

    def test_production_environment_defines_hosted_runtime_contract(self) -> None:
        """Production uses file-backed secrets and shared cluster services."""
        production = dotenv_values(REPOSITORY_ROOT / '.env.production')

        self.assertEqual(
            production,
            {
                'EXECENV_PRODUCTION': 'true',
                'DEBUG': 'false',
                'DJANGO_ENV': 'production',
                'STRUCTURED_LOGGING': 'true',
                'DJANGO_SECRET_FILE': '/etc/secrets/django/secret',
                'CREDENTIALS_KEY_FILE': '/etc/secrets/credentials/key',
                'POSTGRES_URL': (
                    'postgresql://{POSTGRES_USERNAME}:{POSTGRES_PASSWORD}@'
                    'cnpg-database-cluster-rw.cnpg-database.svc.cluster.local:5432/{POSTGRES_DB}'
                ),
                'POSTGRES_USERNAME_FILE': '/etc/secrets/postgres/username',
                'POSTGRES_PASSWORD_FILE': '/etc/secrets/postgres/password',
                'POSTGRES_DB_FILE': '/etc/secrets/postgres/database',
                'REDIS_URL': ('redis://{REDIS_USERNAME}:{REDIS_PASSWORD}@valkey.valkey.svc.cluster.local:6379'),
                'REDIS_USERNAME_FILE': '/etc/secrets/redis/username',
                'REDIS_PASSWORD_FILE': '/etc/secrets/redis/password',
                'REDIS_PREFIX_FILE': '/etc/secrets/redis/prefix',
            },
        )
        self.assertNotIn('CHIEF_LOCAL_DIR', production)

    def test_stage_environment_defines_public_django_settings(self) -> None:
        """Stage limits Django to Chief's public and in-cluster hostnames."""
        stage = dotenv_values(REPOSITORY_ROOT / '.env.production.stage')

        self.assertEqual(
            stage,
            {
                'ALLOWED_HOSTS': ('chief.dev.oivindloe.com,chief-backend.chief-stage.svc.cluster.local'),
                'CSRF_TRUSTED_ORIGINS': 'https://chief.dev.oivindloe.com',
                'LOG_LEVEL': 'debug',
                'SITE_NAME': 'Chief',
            },
        )
        self.assertNotIn('CHIEF_LOCAL_DIR', stage)

    def test_postgres_uses_shared_kubernetes_service(self) -> None:
        """Postgres helpers target the shared CNPG namespace and writer service."""
        postgres = getattr(config.Config.meta, 'postgres_config')

        self.assertEqual(postgres.k8s_namespace, 'cnpg-database')
        self.assertEqual(postgres.k8s_service, 'cnpg-database-cluster-rw')

    def test_compose_target_keeps_local_development_contract(self) -> None:
        """Adding stage leaves Compose target and environment layering unchanged."""
        compose = cast(config.TargetInfo, config.Config.clusters['compose'])
        compose_env = config.Config.envs['compose']
        backend = self._generated_backend_values('compose')

        self.assertEqual(compose.release_name, 'compose')
        self.assertEqual(compose.host, get_local_compose_entrypoint_url())
        self.assertEqual(compose.static_dir, 'backend/.output/static')
        self.assertEqual(compose.version_tag_prefix, 'chief')
        self.assertEqual(compose.version_increments, ['none'])
        self.assertFalse(compose.version_push_tag)
        self.assertEqual(compose.try_creds, ['admin:nimda'])
        self.assertEqual(compose_env.release_name, 'compose')
        self.assertEqual(
            compose_env.env_files,
            ['.env', '.env.development', '.env.development.compose', '?.env.local'],
        )
        self.assertEqual(
            compose_env.substitution_overrides,
            ['infra/docker/overlays/slot-0.env', '.output/compose-slot.env'],
        )
        self.assertEqual(backend['DJANGO_SUPERUSER_USERNAME'], 'admin')
        self.assertEqual(backend['DJANGO_SUPERUSER_EMAIL'], 'admin@localhost')
        self.assertEqual(backend['DJANGO_SUPERUSER_PASSWORD'], 'nimda')
