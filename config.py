# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dev/infra CLI config for chief (the agent orchestrator).

Minimal, compose-only port of the floors config. Wires up the olib `run` CLI
templates we need locally:
  - docker compose (with slot overlays)
  - postgres / redis helpers
  - python roots (lint/mypy/test) + django management passthrough
  - env splitting + versioning for the `compose` target

There are intentionally no kubernetes/CDN/frontend targets yet: the only
deploy target is `compose`.
"""

import os

import parproc as pp

from olib.py.cli.run.templates.base import ConfigMeta
from olib.py.cli.run.templates.django_ import DjangoConfig, django
from olib.py.cli.run.templates.docker import (
    DockerPostgresRestoreDefaults,
    docker,
    get_local_compose_entrypoint_url,
)
from olib.py.cli.run.templates.envs import EnvInfo
from olib.py.cli.run.templates.postgres import postgres
from olib.py.cli.run.templates.py_ import PyRoot
from olib.py.cli.run.templates.redis import redis
from olib.py.cli.run.templates.remote import remote
from olib.py.cli.run.templates.roots import roots
from olib.py.cli.run.templates.version import VersionClusterInfo
from olib.py.cli.run.templates.version import version as version_template


class TargetInfo(VersionClusterInfo):
    """Per-target info. `compose` is the only target for now."""

    release_name: str
    host: str
    try_creds: list[str] | None = None
    basic_auth: bool = False


@docker(
    'infra/docker/docker-compose.yml',
    postgres_restore_defaults=DockerPostgresRestoreDefaults(
        service='chief-postgres',
        database='chief',
        user='admin',
        password='nimda',  # nosec
    ),
)
@postgres()
@redis()
@roots(
    [
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
        ),
        PyRoot('./infra'),
    ],
)
@django()
@remote(plugins=[], default_host='compose')
@version_template
class Config:
    displayName = 'Chief'
    tools = ['python']
    license = 'apache'

    clusters: dict[str, TargetInfo] = {
        'compose': TargetInfo(
            release_name='compose',
            host=get_local_compose_entrypoint_url(),
            version_tag_prefix='chief',
            version_increments=['none'],
            version_push_tag=False,
            try_creds=['admin:nimda'],
        ),
    }

    envs: dict[str, EnvInfo] = {
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


@pp.Proto(name='docker.compose-deps', deps=['prep-dirs'])
def docker_compose_deps(context: pp.ProcContext) -> None:
    """Aggregate job that must exist for `orunr docker compose`. The backend
    container runs migrations itself on startup, so there is nothing else to
    pre-build for the dev stack yet."""
