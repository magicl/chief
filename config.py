# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dev/infra CLI config for Chief, the agent orchestrator.

Wires up the olib `run` CLI templates needed for:
  - docker compose (with slot overlays)
  - postgres / redis helpers
  - python roots (lint/mypy/test) + django management passthrough
  - env splitting + versioning for Compose and the hosted stage target
  - Kubernetes image builds and ArgoCD deployment
"""

import importlib
import os
import sys
from pathlib import Path

import click
import parproc as pp
import sh

from olib.py.cli.run.readonly import readonly_safe
from olib.py.cli.run.templates.assets import (
    AssetsClusterInfo,
    AssetsPathsResult,
    assets,
)
from olib.py.cli.run.templates.base import ConfigMeta, prep_config
from olib.py.cli.run.templates.buildArgoService import (
    BuildContext,
)
from olib.py.cli.run.templates.buildArgoService import (
    ClusterInfo as BuildArgoServiceClusterInfo,
)
from olib.py.cli.run.templates.buildArgoService import (
    buildArgoService,
)
from olib.py.cli.run.templates.cdn import CdnClusterInfoMixin as CdnClusterInfo
from olib.py.cli.run.templates.cdn import CdnInfo, sync_dir_to_cdn
from olib.py.cli.run.templates.django_ import (
    DjangoConfig,
    _primary_django_config,
)
from olib.py.cli.run.templates.django_ import django as django_template
from olib.py.cli.run.templates.docker import (
    DockerPostgresRestoreDefaults,
    docker,
    get_local_compose_entrypoint_url,
)
from olib.py.cli.run.templates.envs import EnvInfo
from olib.py.cli.run.templates.eval_ import eval_cmd
from olib.py.cli.run.templates.js_ import JSRoot
from olib.py.cli.run.templates.postgres import postgres
from olib.py.cli.run.templates.py_ import PyRoot
from olib.py.cli.run.templates.redis import redis
from olib.py.cli.run.templates.remote import remote
from olib.py.cli.run.templates.roots import SubmoduleRoots, roots
from olib.py.cli.run.templates.version import VersionClusterInfo
from olib.py.cli.run.templates.version import version as version_template
from olib.py.cli.run.tools.init.js_ import prepare_js_runtime
from olib.py.cli.run.utils.postgres import PostgresConfig


class TargetInfo(VersionClusterInfo, AssetsClusterInfo):
    """Describe shared Chief target metadata, including Compose."""

    release_name: str
    host: str
    static_dir: str
    try_creds: list[str] | None = None
    basic_auth: bool = False


class ClusterInfo(BuildArgoServiceClusterInfo, CdnClusterInfo, VersionClusterInfo):
    """Describe one deployable Chief Kubernetes target."""

    host: str
    static_dir: str


@buildArgoService(
    BuildContext(
        app_category='apps',
        app_name='chief',
        env_repo_path=Path('~/yolo/env'),
    )
)
@docker(
    'infra/docker/docker-compose.yml',
    postgres_restore_defaults=DockerPostgresRestoreDefaults(
        service='chief-postgres',
        database='chief',
        user='admin',
        password='nimda',  # nosec
    ),
)
@postgres(
    config=PostgresConfig(
        k8s_namespace='cnpg-database',
        k8s_service='cnpg-database-cluster-rw',
    )
)
@redis()
@roots(
    [
        PyRoot(
            '.',
        ),
        PyRoot(
            './backend',
            [
                DjangoConfig(
                    settings='chief.settings',
                    working_dir='./backend',
                    user_table='auth_user',
                    primary=True,
                ),
            ],
            # backend/olib is a symlink to the olib submodule; type-check olib only under its own root.
            noValidate=['olib', 'olib/**'],
        ),
        PyRoot('./infra'),
        JSRoot(
            './backend/apps/web/static/web',
            noValidate=['node_modules/**', 'codemirror/**', 'rich-content/**'],
        ),
        SubmoduleRoots('olib', aliases=['./backend/olib']),
    ],
)
@eval_cmd()
@django_template()
@remote(plugins=[], default_host='compose')
@assets(app_name='chief', asset_paths={'js': '/js'})
@version_template
class Config:
    displayName = 'Chief'
    # GitHub org and repo for managed CI workflows (orun init --github).
    githubOrg = 'magicl'
    githubRepo = 'chief'
    # True → ARC runner label ol-base-{githubOrg}; False → ol-base-{githubRepo}.
    githubInOrg = False
    tools = ['python', 'javascript']
    license = 'apache'
    eval_suites = {'inbox': 'evals.inbox:get_suite'}
    eval_sample_runner = 'evals.inbox:get_sample_runner'
    eval_log_root = '.output/usecase-logs'

    cdns: dict[str, CdnInfo] = {
        'onas': CdnInfo(
            upload_user='oivind',
            base_path='/volume1/infra',
            hostname='onas',
        ),
    }

    clusters: dict[str, ClusterInfo | TargetInfo] = {
        'compose': TargetInfo(
            release_name='compose',
            host=get_local_compose_entrypoint_url(),
            static_dir='backend/.output/static',
            version_tag_prefix='chief',
            version_increments=['none'],
            version_push_tag=False,
            try_creds=['admin:nimda'],
        ),
        'chief-stage': ClusterInfo(
            release_name='chief-stage',
            cluster='dev',
            docker_images={
                'backend': './backend/Dockerfile.prod',
                'static': './infra/k8s/Dockerfile.static.stage',
            },
            registry_url='registry.dev.oivindloe.com',
            default=True,
            cdn='onas',
            host='https://chief.dev.oivindloe.com',
            static_dir='backend/.output/static',
            version_tag_prefix='chief',
            version_increments=['debug'],
            version_push_tag=False,
        ),
    }

    envs: dict[str, EnvInfo] = {
        'chief-stage': EnvInfo(
            release_name='chief-stage',
            env_files=['.env', '.env.production', '.env.production.stage'],
        ),
        'compose': EnvInfo(
            release_name='compose',
            env_files=['.env', '.env.development', '.env.development.compose', '?.env.local'],
            substitution_overrides=[
                'infra/docker/overlays/slot-0.env',
                '.output/compose-slot.env',
            ],
        ),
    }

    meta = ConfigMeta(command_groups=[])


@pp.Proto(name='prep-dirs', deps=[])
def prep_dirs(context: pp.ProcContext) -> None:
    """Create .output dirs up front so host-user owns them before Docker can."""
    for d in ('.output', 'backend/.output'):
        os.makedirs(d, exist_ok=True)


@pp.Proto(
    name='cdn.upload::[target]',
    deps=['django.collectstatic::backend'],
    rdeps=['k8s.deploy::[target]'],
)
def cdn_upload(context: pp.ProcContext, target: str) -> None:
    """Upload Chief's collected backend static files to the target CDN."""
    config = context.params['config']
    cluster_info = ClusterInfo.model_validate(config.clusters[target])
    cdn_info = config.cdns[cluster_info.cdn]
    release_name = cluster_info.release_name
    static_dir = cluster_info.static_dir
    sync_dir_to_cdn(cdn_info, release_name, static_dir, 'public/static')


@pp.Proto(name='js.rich-content-build', deps=['assets.ensure::compose'])
def rich_content_build(context: pp.ProcContext) -> None:
    """Build browser renderer assets into Chief's externally mounted generated lane."""
    static_web_root = 'backend/apps/web/static/web'
    assets_paths = AssetsPathsResult.model_validate(context.results['assets.ensure::compose'])
    js_gen = assets_paths.paths['js_gen']
    prepare_js_runtime([static_web_root])
    sh.pnpm(
        'run',
        'build:rich-content',
        _cwd=static_web_root,
        _env={**os.environ, 'CHIEF_RICH_CONTENT_OUTDIR': js_gen},
        _fg=True,
    )


@pp.Proto(
    name='docker.compose-deps',
    deps=['prep-dirs', 'django.collectstatic::backend', 'js.rich-content-build'],
)
def docker_compose_deps(context: pp.ProcContext) -> None:
    """Aggregate generated and collected static assets before Compose starts."""


def _implement_run_agent() -> click.Command:
    @click.command(
        help='Run one agent turn locally (no Celery / Redis / DB) and print session events.',
        context_settings={'ignore_unknown_options': False},
    )
    @click.argument('user_input')
    @click.option('--provider', help='LLM provider name (e.g. openai, anthropic, local_openai, repeat)')
    @click.option('--model', help='Model name for the provider')
    @click.option('--temperature', type=float, help='Sampling temperature')
    @click.option('--system-prompt', help='System prompt when using --provider/--model')
    @click.option('--spec', help='Full AgentConfigSpec as JSON or YAML string')
    @click.option('--spec-file', help='Path to AgentConfigSpec JSON or YAML file')
    @click.pass_context
    def run_agent(
        ctx: click.Context,
        user_input: str,
        provider: str | None,
        model: str | None,
        temperature: float | None,
        system_prompt: str | None,
        spec: str | None,
        spec_file: str | None,
    ) -> None:
        django_config = _primary_django_config(ctx.obj.meta)
        if django_config is None:
            raise click.ClickException('No Django config found in configured Python roots')

        os.environ.setdefault('DJANGO_SETTINGS_MODULE', django_config.settings)
        os.environ.setdefault('ENV_PATH', '..')
        backend_dir = os.path.abspath(django_config.working_dir)
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        previous_cwd = os.getcwd()
        try:
            os.chdir(backend_dir)
            import django as django_lib

            django_lib.setup()
            run_agent_from_options = importlib.import_module('apps.runner.run_agent').run_agent_from_options

            run_agent_from_options(
                {
                    'input': user_input,
                    'provider': provider,
                    'model': model,
                    'temperature': temperature,
                    'system_prompt': system_prompt,
                    'spec': spec,
                    'spec_file': spec_file,
                },
                stream=click.get_text_stream('stdout'),
            )
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            os.chdir(previous_cwd)

    return readonly_safe(run_agent)


prep_config(Config)
Config.meta.commandGroups.append(('run_agent', _implement_run_agent()))
